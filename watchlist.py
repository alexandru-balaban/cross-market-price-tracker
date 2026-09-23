"""
watchlist.py - lucruri pe care vrei sa le cumperi TU (uz personal).

Cauta pe Kleinanzeigen, in toate categoriile, in Munchen + RAZA_KM (setarea
din compara.py), fiecare produs din watchlist.txt, si te anunta pe Telegram
cand apare un anunt NOU sub pretul maxim pe care l-ai pus.

Rulare manuala:   python3 watchlist.py
In bot:           automat la fiecare WATCHLIST_MINUTE minute (bot.py) + /watch

Format watchlist.txt (un produs pe rand):
    Nume cautat = variante din titlu, -cuvant exclus ; max 120 ; marime 44
  - "max" (obligatoriu) = pretul maxim in EUR
  - "marime" (optional) = arunca anunturile care au ALTA marime in titlu
    ("Gr. 46", "EU 45", "Größe 43"); cele fara marime in titlu raman.
"""

import json
import re
import time
from pathlib import Path

import requests

import compara
import rezumat
import scraper_kleinanzeigen

BASE_DIR = Path(__file__).resolve().parent
FISIER = BASE_DIR / "watchlist.txt"
SEEN_FILE = BASE_DIR / "trimise_watchlist.json"
MAX_PE_PRODUS = 5        # cate anunturi noi trimitem maxim per produs, la o verificare
UITA_DUPA_ZILE = 30


# ============================================================
#  citire / scriere watchlist.txt
# ============================================================

def _parse_linie(line):
    parti = [p.strip() for p in line.split(";")]
    baza, optiuni = parti[0], parti[1:]
    if "=" in baza:
        nume, alias = baza.split("=", 1)
        nume = nume.strip()
        toate = [a.strip() for a in alias.split(",") if a.strip()]
    else:
        nume, toate = baza.strip(), []
    aliasuri = [a for a in toate if not a.startswith("-")] or [nume]
    excluse = [a[1:].strip() for a in toate if a.startswith("-") and a[1:].strip()]

    pret_max, marime = None, None
    for o in optiuni:
        m = re.match(r"max\s*([\d.,]+)", o, re.I)
        if m:
            pret_max = float(m.group(1).replace(",", "."))
        m = re.match(r"m[aă]rime\s*([\d.,]+)", o, re.I)
        if m:
            marime = m.group(1).replace(",", ".")
    if not nume or pret_max is None:
        return None
    return {
        "nume": nume,
        "max": pret_max,
        "marime": marime,
        "regex": compara.construieste_regex(aliasuri),
        "excluse": compara.construieste_regex(excluse, generatii=False) if excluse else None,
    }


def citeste():
    if not FISIER.exists():
        return []
    produse = []
    for line in FISIER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = _parse_linie(line)
        if p:
            produse.append(p)
    return produse


def adauga(linie):
    """Adauga un rand ("Nume ; max 120"). Intoarce produsul sau None daca linia e invalida."""
    p = _parse_linie(linie)
    if not p:
        return None
    text = FISIER.read_text(encoding="utf-8") if FISIER.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    FISIER.write_text(text + linie.strip() + "\n", encoding="utf-8")
    return p


def sterge(nume):
    """Dezactiveaza (pune # in fata) randul cu numele dat. Intoarce True daca l-a gasit."""
    if not FISIER.exists():
        return False
    norm = lambda s: "".join(s.lower().split())
    linii = FISIER.read_text(encoding="utf-8").splitlines()
    gasit = False
    for i, line in enumerate(linii):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if norm(re.split(r"[=;]", s)[0]) == norm(nume):
            linii[i] = "# " + line
            gasit = True
    if gasit:
        FISIER.write_text("\n".join(linii) + "\n", encoding="utf-8")
    return gasit


# ============================================================
#  cautare
# ============================================================

def _url(termen):
    k = compara.slug(termen)
    if compara.RAZA_KM:
        return f"https://www.kleinanzeigen.de/s-{compara.ORAS_DE}/{k}/k0{compara.COD_LOC_DE}r{compara.RAZA_KM}"
    return f"https://www.kleinanzeigen.de/s-{k}/k0"


_MARIME = re.compile(r"(?<![a-zäöü])(?:gr\.?|größe|groesse|gr[öo]ße|eu|size|us|uk)\s*(\d{2}(?:[.,]5)?)(?!\d)", re.I)


def _marime_ok(titlu, marime):
    if not marime:
        return True
    gasite = [m.replace(",", ".") for m in _MARIME.findall(titlu)]
    return not gasite or marime in gasite


def _titlu_ok(titlu, p):
    t = titlu.lower()
    m = p["regex"].search(t)
    if not m:
        return False
    if p["excluse"] and p["excluse"].search(t):
        return False
    if any(c in t for c in compara.CUVINTE_EXCLUSE):
        return False
    inainte = t[:m.start()]  # "Armband für Garmin ..." = accesoriu
    if any(re.search(r"(?<![a-z])" + re.escape(x) + r"(?![a-z])", inainte) for x in compara.PREPOZITII_ACCESORIU):
        return False
    return _marime_ok(titlu, p["marime"])


def cauta(p):
    r = requests.get(_url(p["nume"]), headers=scraper_kleinanzeigen.HEADERS, timeout=20)
    r.raise_for_status()
    anunturi = scraper_kleinanzeigen.parse_html(r.content)
    km = compara.distante_km(r.content)
    if compara.RAZA_KM:
        anunturi = [a for a in anunturi if km.get(str(a["ad_id"]), 0) <= compara.RAZA_KM]
    m = re.search(rb"\d+\s*-\s*\d+\s+von\s+([\d.]+)", r.content)
    if m:
        anunturi = anunturi[:int(m.group(1).decode().replace(".", ""))]

    gasite = []
    for a in anunturi:
        if a["price"] and a["price"] <= p["max"] and _titlu_ok(a["title"], p):
            gasite.append({
                "produs": p["nume"], "max": p["max"], "titlu": a["title"], "url": a["url"],
                "pret": a["price"], "vb": a.get("negotiable"), "km": km.get(str(a["ad_id"])),
                "marime": p["marime"],
            })
    return gasite


def verifica():
    """Intoarce (produse, toate_anunturile_potrivite)."""
    produse = citeste()
    rezultate = []
    for i, p in enumerate(produse):
        try:
            rezultate.extend(cauta(p))
        except Exception as e:
            print(f"Watchlist: eroare la {p['nume']}: {e}")
        if i < len(produse) - 1:
            time.sleep(compara.DELAY)
    return produse, rezultate


# ============================================================
#  anunturi deja trimise
# ============================================================

def _cheie(a):
    return f"{a['url']}|{a['pret']}"


def _load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    limita = time.time() - UITA_DUPA_ZILE * 86400
    return {k: t for k, t in data.items() if t >= limita}


def _save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")


def filtreaza_noi(rezultate):
    seen = _load_seen()
    return [a for a in rezultate if _cheie(a) not in seen]


def marcheaza_trimise(anunturi):
    seen = _load_seen()
    acum = time.time()
    for a in anunturi:
        seen[_cheie(a)] = acum
    _save_seen(seen)


# ============================================================
#  Telegram
# ============================================================

def imbogateste(a):
    """Citeste pagina anuntului: detalii (Zustand, Größe), ridicare/livrare, extras din descriere.
    Intoarce False daca marimea din detalii nu e a ta (anuntul trebuie sarit)."""
    try:
        info = rezumat.detalii_anunt(a["url"])
    except Exception as e:
        print(f"Watchlist: nu am putut citi {a['url']}: {e}")
        return True
    marime_det = info["detalii"].get("Größe")
    if a.get("marime") and marime_det:
        m = re.search(r"\d{2}(?:[.,]5)?", marime_det)
        if m and m.group(0).replace(",", ".") != a["marime"]:
            return False
    a["zustand"] = info["detalii"].get("Zustand")
    a["marime_det"] = marime_det
    a["versand"] = info["versand"]
    a["descriere"] = rezumat.extras(info["descriere"])
    return True


def format_anunt(a, arata_max=True):
    import html
    pret = f"{a['pret']:g} €" + (" VB" if a["vb"] else "")
    maxim = f" (maximul tău: {a['max']:g} €)" if arata_max else ""
    loc = f" · 📍 {a['km']} km" if a["km"] is not None else ""
    linii = [f"👀 <b>{html.escape(a['produs'])}</b>",
             html.escape(a["titlu"]),
             f"💶 {pret}{maxim}{loc}"]
    extra = []
    if a.get("zustand"):
        extra.append(f"Zustand: {html.escape(a['zustand'])}")
    if a.get("marime_det"):
        extra.append(f"Größe {html.escape(a['marime_det'])}")
    if a.get("versand") is False:
        extra.append("doar ridicare")
    elif a.get("versand"):
        extra.append("se poate trimite")
    if extra:
        linii.append("ℹ️ " + " · ".join(extra))
    if a.get("descriere"):
        linii.append(f"\n📝 <i>{html.escape(a['descriere'])}</i>")
    return "\n".join(linii)


def trimite(chat=None, arata_gol=False):
    """Verifica watchlist-ul si trimite anunturile noi. Intoarce cate a trimis."""
    import telegram_notify as tg
    produse, rezultate = verifica()
    if not produse:
        if arata_gol:
            tg.send_message("👀 Watchlist-ul e gol. Adaugă ceva: <code>/adauga Garmin Forerunner 265 ; max 250</code>", chat)
        return 0
    noi = filtreaza_noi(rezultate)
    trimise = []
    pe_produs = {}
    for a in sorted(noi, key=lambda a: a["pret"]):
        if pe_produs.get(a["produs"], 0) >= MAX_PE_PRODUS:
            continue
        ok = imbogateste(a)
        time.sleep(compara.DELAY)  # o cerere in plus pe Kleinanzeigen per anunt
        if not ok:
            continue  # alta marime in detaliile anuntului
        pe_produs[a["produs"]] = pe_produs.get(a["produs"], 0) + 1
        tg.send_message(format_anunt(a), chat, url_buton=a["url"])
        trimise.append(a)
    marcheaza_trimise(noi)  # si cele peste limita, ca sa nu revina la fiecare verificare
    if arata_gol:
        extra = f" ({len(noi) - len(trimise)} sărite: peste limita de {MAX_PE_PRODUS}/produs sau altă mărime)" if len(noi) > len(trimise) else ""
        if trimise:
            tg.send_message(f"👀 Watchlist: {len(trimise)} anunțuri noi{extra}. "
                            f"În total {len(rezultate)} active sub prețul tău, la {len(produse)} produse.", chat)
        else:
            tg.send_message(f"👀 Nimic nou. {len(rezultate)} anunțuri active sub prețul tău "
                            f"(deja trimise), la {len(produse)} produse.", chat)
    return len(trimise)


def gaseste(text):
    """Produsul din watchlist cu numele dat, sau unul ad-hoc din text
    ("Garmin Forerunner 265 ; max 250" - fara "max" = orice pret)."""
    norm = lambda s: "".join(s.lower().split())
    for p in citeste():
        if norm(p["nume"]) == norm(text):
            return p, True
    p = _parse_linie(text if re.search(r";\s*max", text, re.I) else text + " ; max 1000000")
    return p, False


def trimite_unul(text, chat=None, max_anunturi=10):
    """Verifica un singur produs si arata TOATE anunturile active sub pret
    (si pe cele trimise deja), cele mai ieftine primele."""
    import html
    import telegram_notify as tg
    p, din_lista = gaseste(text)
    if not p:
        tg.send_message("❓ Scrie produsul, ex: <code>/watch Garmin Forerunner 265 ; max 250</code>", chat)
        return 0
    gasite = sorted(cauta(p), key=lambda a: a["pret"])
    fara_limita = p["max"] >= 1000000
    limita = "orice preț" if fara_limita else f"max {p['max']:g} €"
    sursa = "din watchlist" if din_lista else "căutare de o dată, nu e în watchlist"
    if not gasite:
        tg.send_message(f"👀 <b>{html.escape(p['nume'])}</b> ({limita}, {sursa}): niciun anunț acum în raza ta.", chat)
        return 0
    tg.send_message(f"👀 <b>{html.escape(p['nume'])}</b> ({limita}, {sursa}): {len(gasite)} anunțuri"
                    + (f", îți arăt cele mai ieftine {max_anunturi}" if len(gasite) > max_anunturi else "") + ":", chat)
    trimise = 0
    for a in gasite:
        if trimise >= max_anunturi:
            break
        ok = imbogateste(a)
        time.sleep(compara.DELAY)
        if not ok:
            continue
        tg.send_message(format_anunt(a, arata_max=not fara_limita), chat, url_buton=a["url"])
        trimise += 1
    if din_lista:
        marcheaza_trimise(gasite)
    return len(gasite)


if __name__ == "__main__":
    produse, rezultate = verifica()
    print(f"{len(produse)} produse in watchlist, {len(rezultate)} anunturi sub pretul maxim:\n")
    for a in sorted(rezultate, key=lambda a: (a["produs"], a["pret"])):
        print(f"  [{a['produs']}] {a['pret']:g} EUR (max {a['max']:g})  {a['titlu'][:60]}")
        print(f"       {a['url']}")
