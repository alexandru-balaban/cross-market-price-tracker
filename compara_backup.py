"""
compara.py - compara preturile intre Germania (Kleinanzeigen, Munchen + raza)
si Moldova (999.md), model cu model, pe mai multe categorii de produse.

Rulare:
    python3 compara.py                  # toate categoriile
    python3 compara.py casti            # o singura categorie (camere / casti / console / controlere)
    python3 compara.py "Sony A7 III"    # un singur model (test rapid)

Categoriile si setarile lor sunt in CATEGORII mai jos; modelele fiecarei
categorii sunt in fisierul ei (modele.txt, modele_casti.txt, ...).

Ce face, pentru fiecare model:
  1. cauta modelul pe Kleinanzeigen (in categoria potrivita, Munchen + RAZA_KM)
     si pe 999.md (in subcategoria potrivita);
  2. pastreaza doar anunturile al caror titlu chiar e despre modelul
     respectiv (ex. "Sony A7 III" nu ia si "A7R III" sau "A7 II");
  3. arunca cererile de cumparare, inchirierile, piesele/defectele,
     accesoriile ("Akku fur Sony A7 III") si preturile absurde;
  4. calculeaza pretul median in fiecare tara (in EUR) si diferenta;
  5. listeaza anunturile individuale care sunt mult sub pretul pietei
     din cealalta tara (potentiale "chilipiruri").

Rezultatele: tabel in terminal + mesaj pe Telegram (vezi telegram_notify.py)
+ istoric in price_monitor.db. CSV-uri in "rapoarte/" doar daca SALVEAZA_CSV = True.
Pentru bot cu comenzi si scanari automate: python3 bot.py

Depinde de scraper_kleinanzeigen.py si scraper_999.py (acelasi folder).
"""

import csv
import json
import os
import re
import sqlite3
import statistics
import sys
import time
from datetime import datetime

import requests

import scraper_999
import scraper_kleinanzeigen

# ============================================================
#  SETARI GENERALE - modifica dupa nevoie
# ============================================================

# Zona din Germania: orasul + raza in km (RAZA_KM = 0 -> toata Germania)
ORAS_DE = "muenchen"
COD_LOC_DE = "l6411"    # codul Kleinanzeigen pentru Munchen
RAZA_KM = 30

PRAG_DIFERENTA = 25     # % - de la ce diferenta intre tari marcam un model
MIN_ANUNTURI = 3        # cate anunturi trebuie sa aiba modelul in FIECARE tara
PAGINI_KLEINANZEIGEN = 1   # 25 anunturi/pagina (o categorie poate avea "pagini_ka" proprii)
DELAY = 4               # secunde intre cereri (nu cobori sub 3)

FOLDER_RAPOARTE = "rapoarte"
DB_PATH = "price_monitor.db"

TRIMITE_TELEGRAM = True   # rezumat + chilipiruri pe Telegram la final
SALVEAZA_CSV = False      # True = salveaza si CSV-uri in rapoarte/

# Cuvinte care elimina un anunt in ORICE categorie (litere mici, oriunde in titlu)
CUVINTE_EXCLUSE = [
    # cereri de cumparare
    "suche", "gesucht", "caut ", "cumpar", "cumpăr", "куплю",
    # piese / defecte
    "defekt", "defect", "kaputt", "ersatzteil", "bastler", "für teile",
    "pentru piese", "pe piese", "запчаст", "не работает",
    # inchirieri
    "miete", "vermiet", "leihen", "închiriere", "inchiriere", "chirie", "аренда", "прокат",
    # atrape / doar cutia
    "attrappe", "dummy", "leerkarton", "nur ovp", "doar cutia",
]

# Daca unul dintre acestea apare INAINTE de numele modelului, e accesoriu
# ("Akku für Sony A7 III", "Husa pentru Canon 600D")
PREPOZITII_ACCESORIU = ["für", "fuer", "for", "pentru", "для", "passend"]

# ============================================================
#  CATEGORII
#    ka_url / ka_cod / ka_filtru = categoria pe Kleinanzeigen
#    id_999 = subcategoria pe 999.md
#    costuri_eur = transport + comisioane estimate per produs
#    filtru_accesorii = aplica regula "für/pentru <model>" = accesoriu
#    cuvinte_excluse = cuvinte eliminate doar in categoria respectiva
# ============================================================
CATEGORII = {
    "camere": {
        "nume": "Camere foto", "emoji": "📷",
        "fisier_modele": "modele.txt",
        "ka_url": "s-foto/camera", "ka_cod": "c245", "ka_filtru": "+foto.art_s:camera",
        "id_999": 77,
        "costuri_eur": 40, "pret_minim_eur": 15,
        "filtru_accesorii": True,
        "cuvinte_excluse": [],
    },
    "casti": {
        "nume": "Căști over-ear", "emoji": "🎧",
        "fisier_modele": "modele_casti.txt",
        # pe Kleinanzeigen castile sunt in Elektronik (cautarea dupa model filtreaza)
        "ka_url": "s-multimedia-elektronik", "ka_cod": "c161", "ka_filtru": "",
        "id_999": 65,
        "costuri_eur": 15, "pret_minim_eur": 20,
        "filtru_accesorii": True,
        "cuvinte_excluse": ["ohrpolster", "polster", "earpads", "ear pads", "ear cushion",
                            "pernite", "pernițe", "амбушюр", "dongle", "ремонт", "reparat"],
    },
    "console": {
        "nume": "Console", "emoji": "🎮",
        "fisier_modele": "modele_console.txt",
        "ka_url": "s-konsolen", "ka_cod": "c279", "ka_filtru": "",
        "pagini_ka": 3,
        "id_999": 5,
        "costuri_eur": 30, "pret_minim_eur": 60,
        "filtru_accesorii": True,
        "cuvinte_excluse": ["ремонт", "reparat", "repair", "прошивк", "abonament", "подписк",
                            "ps plus", "аккаунт", "account", "konto", "ladestation", "tausch",
                            "nur spiel", "nur controller", "doar controller"],
    },
    "controlere": {
        "nume": "Controlere", "emoji": "🕹️",
        "fisier_modele": "modele_controlere.txt",
        "ka_url": "s-konsolen", "ka_cod": "c279", "ka_filtru": "",
        "pagini_ka": 2,
        "id_999": 7673,
        "costuri_eur": 10, "pret_minim_eur": 15,
        "filtru_accesorii": False,
        "cuvinte_excluse": ["ремонт", "reparat", "repair", "ladestation", "ladestaion", "charging",
                            "зарядн", "incarcator", "încărcător", "grip", "thumb", "silikon",
                            "halterung", "ständer", "akku für", "akku fuer", "tausch"],
    },
}

# ============================================================


def citeste_modele(path):
    """Un model pe rand: "Nume = alias1, alias2, -exclus1, -exclus2".
    Aliasurile cu "-" in fata elimina anunturile care le contin
    (ex. "PS5 = ps5, -pro" nu ia si "PS5 Pro")."""
    modele = []
    if not os.path.exists(path):
        return modele
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                nume, alias = line.split("=", 1)
                nume = nume.strip()
                toate = [a.strip() for a in alias.split(",") if a.strip()]
            else:
                nume = line
                toate = []
            aliasuri = [a for a in toate if not a.startswith("-")]
            excluse = [a[1:].strip() for a in toate if a.startswith("-") and a[1:].strip()]
            if not aliasuri:
                # fara aliasuri: numele fara marca ("Canon 600D" -> "600d")
                parts = nume.split(" ", 1)
                aliasuri = [parts[1] if len(parts) > 1 else nume]
            modele.append({
                "nume": nume,
                "regex": construieste_regex(aliasuri),
                "excluse": construieste_regex(excluse, generatii=False) if excluse else None,
            })
    return modele


def toate_modele(categorie=None):
    """Lista de modele din toate categoriile (sau doar din una), cu cheia categoriei atasata."""
    rezultat = []
    for cheie, cat in CATEGORII.items():
        if categorie and cheie != categorie:
            continue
        for m in citeste_modele(cat["fisier_modele"]):
            m["categorie"] = cheie
            rezultat.append(m)
    return rezultat


def construieste_regex(aliasuri, generatii=True):
    """Din "a7 iii" face un regex care prinde "A7 III", "a7iii", "A7-III",
    dar nu "A7R III", "A7 II", "A7 IIII" sau "A7 III" precedat de litere/cifre.
    Dupa model nu are voie sa urmeze o alta generatie (II/III/IV/Mark),
    ca "z6" sa nu prinda "Z6 II"."""
    sep = r"[\s\-_/.]*"
    variante = []
    for a in aliasuri:
        chars = [re.escape(c) for c in a.lower() if not c.isspace() and c not in "-_/."]
        if chars:
            variante.append(sep.join(chars))
    corp = "|".join(variante)
    return re.compile(
        # inainte: nimic alfanumeric, sau "eos" lipit (Canon "EOS600D")
        r"(?:(?<![a-z0-9])|(?<=eos))(?:" + corp + r")(?![a-z0-9])"
        + (r"(?!" + sep + r"(?:ii|iii|iv|mark|mk)(?![a-z]))" if generatii else ""),
        re.IGNORECASE,
    )


def titlu_valid(titlu, model, cat):
    t = titlu.lower()
    m = model["regex"].search(t)
    if not m:
        return False
    if model["excluse"] and model["excluse"].search(t):
        return False
    if any(c in t for c in CUVINTE_EXCLUSE) or any(c in t for c in cat["cuvinte_excluse"]):
        return False
    if cat["filtru_accesorii"]:
        inainte = t[:m.start()]
        if any(re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", inainte) for p in PREPOZITII_ACCESORIU):
            return False
    return True


# ---------------- Kleinanzeigen ----------------

def slug(text):
    s = text.lower().strip()
    s = re.sub(r"[^a-z0-9äöüß]+", "-", s)
    return s.strip("-")


def url_kleinanzeigen(cat, termen, pagina):
    seite = f"seite:{pagina}/" if pagina > 1 else ""
    k = slug(termen)
    if RAZA_KM:
        return (f"https://www.kleinanzeigen.de/{cat['ka_url']}/{ORAS_DE}/{seite}{k}/k0"
                f"{cat['ka_cod']}{COD_LOC_DE}r{RAZA_KM}{cat['ka_filtru']}")
    return f"https://www.kleinanzeigen.de/{cat['ka_url']}/{seite}{k}/k0{cat['ka_cod']}{cat['ka_filtru']}"


def distante_km(content):
    """{ad_id: km} din pagina de rezultate - fiecare anunt arata "(12 km)" langa oras."""
    d = {}
    parti = re.split(rb'data-adid="(\d+)"', content)
    for i in range(1, len(parti) - 1, 2):
        m = re.search(rb"\((\d+)\s*km\)", parti[i + 1])
        if m:
            d[parti[i].decode()] = int(m.group(1))
    return d


def cauta_kleinanzeigen(cat, termen):
    rezultate = []
    for pagina in range(1, cat.get("pagini_ka", PAGINI_KLEINANZEIGEN) + 1):
        r = requests.get(url_kleinanzeigen(cat, termen, pagina),
                         headers=scraper_kleinanzeigen.HEADERS, timeout=20)
        r.raise_for_status()
        anunturi = scraper_kleinanzeigen.parse_html(r.content)
        n_pagina = len(anunturi)

        # Cand sunt putine rezultate in raza, Kleinanzeigen completeaza lista cu
        # anunturi mai departe. Le aruncam pe cele peste RAZA_KM si pastram
        # doar cate zice titlul paginii.
        if RAZA_KM:
            km = distante_km(r.content)
            anunturi = [a for a in anunturi if km.get(str(a["ad_id"]), 0) <= RAZA_KM]

        m = re.search(rb"\d+\s*-\s*\d+\s+von\s+([\d.]+)", r.content)
        if m:
            total = int(m.group(1).decode().replace(".", ""))
            deja = (pagina - 1) * 25
            anunturi = anunturi[:max(0, total - deja)]

        for a in anunturi:
            if a["price"]:
                rezultate.append({
                    "sursa": "DE", "id": a["ad_id"], "titlu": a["title"], "url": a["url"],
                    "pret_eur": a["price"], "pret_original": a["price"], "moneda": "EUR",
                })
        if n_pagina < 25:
            break
        time.sleep(DELAY)
    return rezultate


# ---------------- 999.md ----------------

def cauta_999(cat, termen, rates):
    payload = {
        "operationName": "SearchAds",
        "query": scraper_999.QUERY,
        "variables": {"input": {
            "source": "AD_SOURCE_DESKTOP_REDESIGN",
            "sort": "SORT_ADS_DATE_DESC",
            "pagination": {"limit": 78, "skip": 0},
            "filters": [],
            "subCategoryId": cat["id_999"],
            "query": termen,
        }},
    }
    r = requests.post(scraper_999.GRAPHQL_URL, headers=scraper_999.HEADERS,
                      data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    rezultate = []
    for ad in data["data"]["searchAds"]["ads"] or []:
        p = scraper_999.parse_ad(ad, rates)   # arunca deja "Cumpăr"
        if p and p["price"]:
            rezultate.append({
                "sursa": "MD", "id": p["ad_id"], "titlu": p["title"], "url": p["url"],
                "pret_eur": p["price"], "pret_original": p["price_original"], "moneda": p["currency"],
            })

    return rezultate


# ---------------- analiza ----------------

def curata(anunturi, model, cat):
    """Filtre pe titlu + duplicate + preturi absurde fata de mediana."""
    buni, vazute = [], set()
    for a in anunturi:
        if a["pret_eur"] < cat["pret_minim_eur"] or not titlu_valid(a["titlu"], model, cat):
            continue
        # pe 999 acelasi vanzator reposteaza des acelasi anunt
        cheie = (re.sub(r"\W+", "", a["titlu"].lower()), round(a["pret_eur"]))
        if a["id"] in vazute or cheie in vazute:
            continue
        vazute.add(a["id"])
        vazute.add(cheie)
        buni.append(a)
    if len(buni) >= 4:
        med = statistics.median(a["pret_eur"] for a in buni)
        # sub 40% din mediana = de obicei accesoriu/piesa; peste 250% = set mare
        buni = [a for a in buni if 0.4 * med <= a["pret_eur"] <= 2.5 * med]
    return buni


def statistici(anunturi):
    preturi = sorted(a["pret_eur"] for a in anunturi)
    if not preturi:
        return {"n": 0, "mediana": None, "min": None}
    return {"n": len(preturi), "mediana": round(statistics.median(preturi)), "min": round(preturi[0])}


def analizeaza(nume, categorie, de, md):
    costuri = CATEGORII[categorie]["costuri_eur"]
    s_de, s_md = statistici(de), statistici(md)
    rand = {
        "categorie": categorie, "model": nume,
        "n_DE": s_de["n"], "mediana_DE": s_de["mediana"], "min_DE": s_de["min"],
        "n_MD": s_md["n"], "mediana_MD": s_md["mediana"], "min_MD": s_md["min"],
        "diferenta_%": None, "directie": "", "profit_estimat_EUR": None, "semnal": "",
    }
    chilipiruri = []
    if not s_de["mediana"] or not s_md["mediana"]:
        rand["semnal"] = "date insuficiente"
        return rand, chilipiruri

    if s_md["mediana"] >= s_de["mediana"]:
        ieftin, scump, directie, lista_ieftina = s_de, s_md, "cumpari DE -> vinzi MD", de
    else:
        ieftin, scump, directie, lista_ieftina = s_md, s_de, "cumpari MD -> vinzi DE", md

    dif = (scump["mediana"] - ieftin["mediana"]) / ieftin["mediana"] * 100
    rand["diferenta_%"] = round(dif, 1)
    rand["directie"] = directie
    rand["profit_estimat_EUR"] = round(scump["mediana"] - ieftin["mediana"] - costuri)

    destule = s_de["n"] >= MIN_ANUNTURI and s_md["n"] >= MIN_ANUNTURI
    if not destule:
        rand["semnal"] = "putine anunturi"
    elif dif >= PRAG_DIFERENTA and rand["profit_estimat_EUR"] > 0:
        rand["semnal"] = "*** OPORTUNITATE ***"

    # Anunturi individuale din tara ieftina, mult sub mediana tarii scumpe
    if destule:
        for a in lista_ieftina:
            profit = scump["mediana"] - a["pret_eur"] - costuri
            sub = (scump["mediana"] - a["pret_eur"]) / scump["mediana"] * 100
            if sub >= PRAG_DIFERENTA and profit > 0:
                chilipiruri.append({
                    "categorie": categorie, "model": nume, "tara": a["sursa"], "titlu": a["titlu"],
                    "pret_EUR": round(a["pret_eur"]),
                    "pret_original": f"{a['pret_original']:g} {a['moneda']}",
                    "mediana_tara_cealalta": scump["mediana"],
                    "sub_mediana_%": round(sub, 1), "profit_estimat_EUR": round(profit),
                    "url": a["url"],
                })
    return rand, chilipiruri


# ---------------- salvare ----------------

def scrie_csv(path, randuri):
    if not randuri:
        return
    # utf-8-sig ca Excel sa afiseze corect diacriticele
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(randuri[0].keys()))
        w.writeheader()
        w.writerows(randuri)


def salveaza_istoric(run_at, model, anunturi):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_at TEXT, model TEXT, sursa TEXT, ad_id TEXT, titlu TEXT, url TEXT,
            pret_eur REAL, pret_original REAL, moneda TEXT
        )
    """)
    conn.executemany("""
        INSERT INTO model_prices (run_at, model, sursa, ad_id, titlu, url, pret_eur, pret_original, moneda)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(run_at, model, a["sursa"], a["id"], a["titlu"], a["url"], a["pret_eur"],
           a["pret_original"], a["moneda"]) for a in anunturi])
    conn.commit()
    conn.close()


def fmt(v, suffix=""):
    return "-" if v is None else f"{v}{suffix}"


# ---------------- rulare ----------------

def selecteaza(tinta=None):
    """tinta = None (tot), cheia unei categorii ("casti") sau numele unui model."""
    if not tinta:
        return toate_modele()
    t = tinta.strip().lower()
    if t in CATEGORII:
        return toate_modele(t)
    norm = lambda s: "".join(s.lower().split())
    modele = [m for m in toate_modele() if norm(m["nume"]) == norm(t)]
    if not modele:
        raise ValueError(f"Nu gasesc '{tinta}' - nici categorie ({', '.join(CATEGORII)}), nici model din fisierele de modele.")
    return modele


def durata_estimata_min(n_modele):
    return n_modele * (DELAY * 2 + 2) // 60 + 1


def ruleaza(tinta=None):
    """Scaneaza modelele si intoarce (randuri, chilipiruri, run_at). Folosit de main() si de bot.py."""
    modele = selecteaza(tinta)
    rates = scraper_999.get_rates()
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    zona = f"{ORAS_DE} + {RAZA_KM} km" if RAZA_KM else "toata Germania"
    print(f"{len(modele)} modele | DE: {zona} | prag {PRAG_DIFERENTA}%")
    print(f"Durata estimata: ~{durata_estimata_min(len(modele))} min\n")

    randuri, toate_chilipiruri = [], []
    for i, m in enumerate(modele, 1):
        cat = CATEGORII[m["categorie"]]
        print(f"[{i}/{len(modele)}] {cat['emoji']} {m['nume']} ... ", end="", flush=True)
        try:
            de = curata(cauta_kleinanzeigen(cat, m["nume"]), m, cat)
            time.sleep(DELAY)
            md = curata(cauta_999(cat, m["nume"], rates), m, cat)
        except Exception as e:
            print(f"EROARE: {e}")
            time.sleep(DELAY)
            continue
        rand, chil = analizeaza(m["nume"], m["categorie"], de, md)
        randuri.append(rand)
        toate_chilipiruri.extend(chil)
        salveaza_istoric(run_at, m["nume"], de + md)
        print(f"DE {rand['n_DE']} anunturi, mediana {fmt(rand['mediana_DE'], ' EUR')} | MD "
              f"{rand['n_MD']} anunturi, mediana {fmt(rand['mediana_MD'], ' EUR')}")
        if i < len(modele):
            time.sleep(DELAY)

    toate_chilipiruri.sort(key=lambda c: c["profit_estimat_EUR"], reverse=True)
    return randuri, toate_chilipiruri, run_at


def afiseaza(randuri, toate_chilipiruri):
    comparabile = [r for r in randuri if r["diferenta_%"] is not None]
    comparabile.sort(key=lambda r: r["diferenta_%"], reverse=True)

    print("\n" + "=" * 100)
    print(f"{'MODEL':<28}{'DE med':>8}{'n':>4}{'MD med':>9}{'n':>4}{'dif %':>8}{'profit':>8}   DIRECTIE / SEMNAL")
    print("-" * 100)
    for r in comparabile:
        print(f"{r['model'][:27]:<28}{r['mediana_DE']:>8}{r['n_DE']:>4}{r['mediana_MD']:>9}{r['n_MD']:>4}"
              f"{r['diferenta_%']:>8}{r['profit_estimat_EUR']:>8}   {r['directie']}  {r['semnal']}")
    lipsa = [r["model"] for r in randuri if r["diferenta_%"] is None]
    if lipsa:
        print(f"\nFara anunturi intr-una din tari: {', '.join(lipsa)}")

    if toate_chilipiruri:
        print(f"\nTOP ANUNTURI SUB PRETUL PIETEI DIN CEALALTA TARA ({len(toate_chilipiruri)} in total):")
        for c in toate_chilipiruri[:15]:
            print(f"  [{c['tara']}] {c['pret_EUR']} EUR  (mediana cealalta tara {c['mediana_tara_cealalta']}, "
                  f"profit ~{c['profit_estimat_EUR']} EUR)  {c['titlu'][:60]}")
            print(f"       {c['url']}")


def main():
    tinta = " ".join(sys.argv[1:]) or None
    try:
        randuri, toate_chilipiruri, run_at = ruleaza(tinta)
    except ValueError as e:
        print(e)
        return
    afiseaza(randuri, toate_chilipiruri)

    if SALVEAZA_CSV:
        os.makedirs(FOLDER_RAPOARTE, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        scrie_csv(os.path.join(FOLDER_RAPOARTE, f"raport_{stamp}.csv"), randuri)
        scrie_csv(os.path.join(FOLDER_RAPOARTE, f"chilipiruri_{stamp}.csv"), toate_chilipiruri)
        print(f"\nRapoarte salvate in {FOLDER_RAPOARTE}/ (raport_{stamp}.csv, chilipiruri_{stamp}.csv)")

    if TRIMITE_TELEGRAM:
        import telegram_notify
        try:
            # la scanarea unui singur model trimitem tot, altfel doar anunturile noi
            un_model = tinta is not None and tinta.strip().lower() not in CATEGORII
            n = telegram_notify.trimite_rezultate(randuri, toate_chilipiruri, run_at,
                                                  doar_noi=not un_model)
            print(f"\nTrimis pe Telegram: rezumat + {n} anunturi.")
        except telegram_notify.NeconfiguratError as e:
            print(f"\nTelegram nu e configurat ({e}) - rezultatele sunt doar in terminal.")
        except Exception as e:
            print(f"\nNu am putut trimite pe Telegram: {e}")

    print("Atentie: medianele amesteca variante (body/kit, cu/fara accesorii) - verifica anunturile inainte sa cumperi.")


if __name__ == "__main__":
    main()
