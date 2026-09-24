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

Viteza: pe 999.md, pentru fiecare categorie se alege singur varianta mai
rapida - descarca toata categoria o data (daca are mai putine pagini decat
modele de cautat) sau cauta model cu model. Pauza DELAY se face doar intre
cererile la Kleinanzeigen; intre site-uri diferite nu se mai asteapta.

Rezultatele: tabel in terminal + mesaj pe Telegram (vezi telegram_notify.py)
+ istoric in price_monitor.db. CSV-uri in "rapoarte/" doar daca SALVEAZA_CSV = True.
Pentru bot cu comenzi si scanari automate: python3 bot.py

La fiecare scanare comparam si cu scanarea anterioara a aceluiasi model, ca
sa vedem ce anunturi au disparut intre timp (probabil vandute) - vezi
actualizeaza_disparitii() / statistici_vanzari() si verifica_vanzari.py.

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
DELAY = 4               # secunde intre cereri la Kleinanzeigen (nu cobori sub 3);
                        # folosit si de watchlist.py
DELAY_999 = 1.5         # secunde intre paginile 999.md cand se descarca o categorie

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
    "monitoare": {
        "nume": "Monitoare gaming", "emoji": "🖥️",
        "fisier_modele": "modele_monitoare.txt",
        # doar modelele DIN LISTA (modele_monitoare.txt) sunt monitorizate, si lista
        # contine doar modele cu minim 1920x1080 (Full HD) si minim 144Hz - pragul se
        # aplica la selectia modelelor din fisier, nu se verifica din titlul anuntului
        "ka_url": "s-pc-zubehoer-software/monitore", "ka_cod": "c225",
        "ka_filtru": "+pc_zubehoer_software.art_s:monitore",
        "id_999": 10,
        "costuri_eur": 35, "pret_minim_eur": 50,
        "filtru_accesorii": True,
        "cuvinte_excluse": ["ремонт", "reparat", "repair", "defekt", "kaputt", "riss",
                            "ersatzteile", "piese", "doar suport", "doar picior", "nur fuß",
                            "nur standfuß", "monitorarm", "halterung", "ständer"],
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


# Memorate per categorie, golite la inceputul fiecarei scanari (ruleaza()) -
# ca editarile din modele*.txt sa se vada la scanarea urmatoare fara sa
# reparsam fisierul la fiecare anunt in parte.
_CUVINTE_DISTINCTE_CACHE = {}

# Peste cate modele cu ACELASI prefix comun (ex. "Canon" la 30+ modele Canon)
# consideram acel prefix prea generic ca sa mai insemne o "familie" de variante
# inrudite (ex. "PS5" la doar 3 modele: PS5, PS5 Pro, PS5 Slim) - si NU mai
# generam relatii alt_model_in_titlu din el. Fara acest prag, orice model
# Canon/Nikon/Sony ar fi marcat "frate" cu ZECI de alte modele ale aceleiasi
# marci (singurul lucru comun fiind numele marcii, nu o gama anume), si un
# anunt legitim care doar mentioneaza un alt model compatibil ("obiectiv
# potrivit si pt. 600D/700D") ar fi respins gresit.
PRAG_MARIME_GRUP_FRATI = 5


def _cuvinte_distincte_categorie(categorie):
    """Pentru fiecare model dintr-o categorie, cuvintele care apartin unui
    "frate" cu care modelul imparte un prefix comun SI restrans (vezi
    PRAG_MARIME_GRUP_FRATI) - ex. "PS5 Pro" si "PS5 Slim" impart "PS5"
    (doar 3 modele il folosesc) -> cuvantul distinct pt. PS5 Pro e "slim",
    cel pt. PS5 Slim e "pro"; "Canon EOS R" si "Canon EOS RP" impart
    "Canon EOS" (doar 2 modele) -> distinct pt. R e "rp". Cuvintele de sub
    3 litere sunt ignorate (prea generice/riscante ca potriviri de sine
    statatoare, ex. "R", "II"). Folosit de alt_model_in_titlu ca sa prinda
    anunturi tip "magazin" ce listeaza mai multe variante intr-un titlu,
    fara sa scrie fraza completa a fiecareia (ex. "Playstation 5 PRO /
    Slim" - "Slim" nu e lipit de "Playstation 5", deci fraza "ps5 slim" nu
    se potriveste)."""
    if categorie in _CUVINTE_DISTINCTE_CACHE:
        return _CUVINTE_DISTINCTE_CACHE[categorie]
    modele = toate_modele(categorie)
    # cate modele incep cu fiecare prefix posibil (tuplu de cuvinte, lowercase) -
    # ca sa deosebim o gama restransa (ex. "ps5" -> 3 modele) de marca intreaga
    # (ex. "canon" -> 30+ modele).
    marime_grup = {}
    for m in modele:
        cuvinte = tuple(w.lower() for w in m["nume"].split())
        for lungime in range(1, len(cuvinte) + 1):
            prefix = cuvinte[:lungime]
            marime_grup[prefix] = marime_grup.get(prefix, 0) + 1
    rezultat = {}
    for m in modele:
        cuvinte_m = m["nume"].split()
        distincte = set()
        for alt in modele:
            if alt is m:
                continue
            cuvinte_alt = alt["nume"].split()
            comun = 0
            for a, b in zip(cuvinte_m, cuvinte_alt):
                if a.lower() == b.lower():
                    comun += 1
                else:
                    break
            # prefix comun de macar un cuvant SI celalalt model are ceva dupa acel prefix
            if comun >= 1 and comun < len(cuvinte_alt):
                prefix = tuple(w.lower() for w in cuvinte_m[:comun])
                if marime_grup.get(prefix, 0) > PRAG_MARIME_GRUP_FRATI:
                    continue  # prefix prea generic (marca intreaga) - sarim
                cuvant = cuvinte_alt[comun].lower()
                if len(cuvant) >= 3:
                    distincte.add(cuvant)
        rezultat[m["nume"]] = distincte
    _CUVINTE_DISTINCTE_CACHE[categorie] = rezultat
    return rezultat


def alt_model_in_titlu(titlu, model):
    """True daca titlul contine, ca CUVANT DE SINE STATATOR, ceva ce apartine
    altui model din aceeasi categorie (vezi _cuvinte_distincte_categorie) -
    semn ca anuntul listeaza mai multe variante/produse (anunt de magazin),
    nu un singur produs, deci pretul afisat poate sa nu corespunda modelului
    cautat. Nu necesita ca varianta sa fie scrisa ca fraza completa langa
    numele marcii - de-aia exista, pe langa excluderile "-cuvant" din
    modele*.txt (care prind doar fraze complete lipite de model)."""
    distincte = _cuvinte_distincte_categorie(model["categorie"]).get(model["nume"])
    if not distincte:
        return False
    t = titlu.lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(c) + r"(?![a-z0-9])", t) for c in distincte)


# Memorate per categorie, golite la inceputul fiecarei scanari (ruleaza()) -
# lista de modele ale categoriei, ca sa n-o recitim din fisier la fiecare
# anunt (vezi alt_model_valid_in_titlu).
_MODELE_CATEGORIE_CACHE = {}


def _modele_categorie(categorie):
    if categorie not in _MODELE_CATEGORIE_CACHE:
        _MODELE_CATEGORIE_CACHE[categorie] = toate_modele(categorie)
    return _MODELE_CATEGORIE_CACHE[categorie]


def alt_model_valid_in_titlu(titlu, model, cat):
    """True daca titlul se potriveste integral (fraza completa + fara
    excluderile lui, exact ca titlu_valid) si cu un ALT model din aceeasi
    categorie - nu neaparat "frate" de-al modelului cautat (vezi
    alt_model_in_titlu de mai sus, care prinde doar variante ale ACELUIASI
    model), ci orice alt produs monitorizat. Prinde anunturile de magazin
    care listeaza produse complet diferite in acelasi titlu, ex.
    "Nintendo Switch si Sony PlayStation 5" sau "Xbox Series S, X / Sony
    PlayStation 5" - niciun model nu poate fi "frate" cu celalalt (nu impart
    niciun prefix din nume), deci alt_model_in_titlu nu le prinde, dar
    fiecare se potriveste, separat, cu fraza lui completa. Cand se intampla
    asta pretul afisat nu se poate atribui cu incredere unui singur produs.

    Risc cunoscut: un anunt legitim cu UN singur produs poate mentiona
    incidental alt model monitorizat (ex. "PS5, eventual schimb cu Switch")
    si sa fie respins gresit - e o compensare deliberata, in favoarea
    increderii in pretul afisat pentru chilipiruri."""
    for alt in _modele_categorie(model["categorie"]):
        if alt["nume"] == model["nume"]:
            continue
        if titlu_valid(titlu, alt, cat):
            return True
    return False


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


def cauta_kleinanzeigen(cat, termen, sesiune=None):
    """sesiune = requests.Session() refolosita intre cereri (mai rapid); optional."""
    http = sesiune or requests
    rezultate = []
    ultima = cat.get("pagini_ka", PAGINI_KLEINANZEIGEN)
    for pagina in range(1, ultima + 1):
        r = http.get(url_kleinanzeigen(cat, termen, pagina),
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
        if n_pagina < 25 or pagina == ultima:
            break      # fara pauza dupa ultima pagina (o face ruleaza intre modele)
        time.sleep(DELAY)
    return rezultate


# ---------------- 999.md ----------------

def cauta_999(cat, termen, rates, sesiune=None):
    http = sesiune or requests
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
    r = http.post(scraper_999.GRAPHQL_URL, headers=scraper_999.HEADERS,
                  data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return _anunturi_999(data["data"]["searchAds"]["ads"], rates)


def _anunturi_999(ads, rates):
    rezultate = []
    for ad in ads or []:
        p = scraper_999.parse_ad(ad, rates)   # arunca deja "Cumpăr"
        if p and p["price"]:
            rezultate.append({
                "sursa": "MD", "id": p["ad_id"], "titlu": p["title"], "url": p["url"],
                "pret_eur": p["price"], "pret_original": p["price_original"], "moneda": p["currency"],
            })
    return rezultate


def _pagina_999(cat, skip, limit, sesiune):
    """O pagina din toata subcategoria (fara cuvant cautat)."""
    payload = {
        "operationName": "SearchAds",
        "query": scraper_999.QUERY,
        "variables": {"input": {
            "source": "AD_SOURCE_DESKTOP_REDESIGN",
            "sort": "SORT_ADS_DATE_DESC",
            "pagination": {"limit": limit, "skip": skip},
            "filters": [],
            "subCategoryId": cat["id_999"],
        }},
    }
    r = sesiune.post(scraper_999.GRAPHQL_URL, headers=scraper_999.HEADERS,
                     data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]["searchAds"]


def descarca_categoria_999(cat, rates, sesiune):
    """Toate anunturile din subcategoria 999.md, pagina cu pagina."""
    pas = scraper_999.PAGE_SIZE
    rezultate, skip = [], 0
    while True:
        bloc = _pagina_999(cat, skip, pas, sesiune)
        ads = bloc.get("ads") or []
        rezultate.extend(_anunturi_999(ads, rates))
        skip += pas
        if len(ads) < pas or skip >= (bloc.get("count") or 0):
            return rezultate
        time.sleep(DELAY_999)


def pregateste_999(modele, rates, sesiune):
    """Pentru fiecare categorie din scanare decide: descarcam toata categoria
    o data (daca are mai putine pagini decat modele de cautat) sau cautam
    model cu model. Intoarce {cheie_categorie: anunturi} pt. cele descarcate."""
    pe_categorii = {}
    for m in modele:
        pe_categorii[m["categorie"]] = pe_categorii.get(m["categorie"], 0) + 1
    descarcate = {}
    for cheie, n_modele in pe_categorii.items():
        cat = CATEGORII[cheie]
        try:
            total = _pagina_999(cat, 0, 1, sesiune).get("count") or 0
            pagini = -(-total // scraper_999.PAGE_SIZE)
            if pagini >= n_modele:
                continue
            print(f"999.md {cat['emoji']} {cat['nume']}: descarc toata categoria "
                  f"({total} anunturi, {pagini} cereri in loc de {n_modele} cautari) ... ", end="", flush=True)
            descarcate[cheie] = descarca_categoria_999(cat, rates, sesiune)
            print(f"{len(descarcate[cheie])} oferte")
        except Exception as e:
            descarcate.pop(cheie, None)
            print(f"\n999.md {cat['nume']}: nu am putut descarca categoria ({e}) - caut model cu model")
        time.sleep(DELAY_999)
    return descarcate


# ---------------- analiza ----------------

def curata(anunturi, model, cat):
    """Filtre pe titlu + duplicate + preturi absurde fata de mediana."""
    buni, vazute = [], set()
    for a in anunturi:
        if a["pret_eur"] < cat["pret_minim_eur"] or not titlu_valid(a["titlu"], model, cat):
            continue
        if alt_model_in_titlu(a["titlu"], model):
            continue   # anunt cu mai multe variante ale ACELUIASI model - pretul poate sa nu corespunda
        if alt_model_valid_in_titlu(a["titlu"], model, cat):
            continue   # anunt tip magazin cu alt produs monitorizat, complet diferit, in acelasi titlu
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_prices_model_run ON model_prices(model, run_at)")
    conn.executemany("""
        INSERT INTO model_prices (run_at, model, sursa, ad_id, titlu, url, pret_eur, pret_original, moneda)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(run_at, model, a["sursa"], a["id"], a["titlu"], a["url"], a["pret_eur"],
           a["pret_original"], a["moneda"]) for a in anunturi])
    conn.commit()
    conn.close()


# ---------------- istoric: dispar anunturi = probabil vandute ----------------
# (vezi verifica_vanzari.py pentru raportul complet si limitarile aproximarii)
#
# Doua reguli invatate testand pe datele reale din prima zi (fara ele, ~97%
# din "disparitii" erau zgomot, nu vanzari - vezi verifica_vanzari.py):
#  1. PRAG_ORE_INTRE_SCANARI: nu comparam doua scanari facute la mai putin de
#     o ora distanta (reveniri rapide manuale in acelasi test), ca sa nu
#     confundam reordonarea paginii cu vanzari.
#  2. Un anunt numaram ca "disparut" doar daca a fost vazut in CEL PUTIN doua
#     scanari inainte sa dispara (nu doar una) - un anunt vazut o singura
#     data si apoi absent e aproape sigur doar iesit de pe pagina 1
#     (categoriile cu o singura pagina scanata gasesc anunturi noi la fiecare
#     rulare, care impinge in afara paginii anunturi mai vechi, dar inca
#     active), nu neaparat vandut.
PRAG_ORE_INTRE_SCANARI = 1.0


def _ore_intre(t1, t2):
    """Diferenta in ore intre doua timestamp-uri "YYYY-MM-DD HH:MM"."""
    f = "%Y-%m-%d %H:%M"
    return round((datetime.strptime(t2, f) - datetime.strptime(t1, f)).total_seconds() / 3600, 1)


def _creeaza_tabel_disparitii(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS disparitii (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT, categorie TEXT, sursa TEXT, ad_id TEXT,
            titlu TEXT, url TEXT, pret_eur REAL,
            prima_data TEXT, ultima_data TEXT, disparut_la TEXT, ore_pe_piata REAL,
            UNIQUE(model, sursa, ad_id)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_disparitii_model_sursa ON disparitii(model, sursa)")


def actualizeaza_disparitii(model_nume, categorie, run_at):
    """Compara anunturile modelului din scanarea CURENTA (run_at) cu cele din
    scanarea ANTERIOARA a aceluiasi model si inregistreaza in tabelul
    'disparitii' pe cele care nu mai apar - probabil vandute sau scoase de
    pe site. Nu face nimic daca e prima scanare a modelului (nu exista
    "anterior" cu care sa compare). Intoarce cate anunturi noi a inregistrat.

    E doar o aproximare (vezi verifica_vanzari.py): un anunt poate disparea
    si pentru ca a iesit din paginile scanate, nu neaparat ca s-a vandut.
    Cu cat se aduna mai multe scanari, cu atat statistica devine mai de
    incredere - de-asta se face la fiecare rulare, nu doar o data."""
    conn = sqlite3.connect(DB_PATH)
    _creeaza_tabel_disparitii(conn)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT run_at FROM model_prices WHERE model=? AND run_at<? ORDER BY run_at DESC LIMIT 1",
                (model_nume, run_at))
    row = cur.fetchone()
    if not row:
        conn.close()
        return 0
    run_anterior = row[0]
    if _ore_intre(run_anterior, run_at) < PRAG_ORE_INTRE_SCANARI:
        conn.close()
        return 0

    cur.execute("SELECT sursa, ad_id, titlu, url, pret_eur FROM model_prices WHERE model=? AND run_at=?",
                (model_nume, run_anterior))
    anterioare = {(s, aid): (t, u, p) for s, aid, t, u, p in cur.fetchall()}
    cur.execute("SELECT DISTINCT sursa, ad_id FROM model_prices WHERE model=? AND run_at=?",
                (model_nume, run_at))
    curente = {(s, aid) for s, aid in cur.fetchall()}

    n = 0
    for (sursa, ad_id), (titlu, url, pret) in anterioare.items():
        if (sursa, ad_id) in curente:
            continue
        cur.execute("SELECT MIN(run_at) FROM model_prices WHERE model=? AND sursa=? AND ad_id=?",
                    (model_nume, sursa, ad_id))
        prima = cur.fetchone()[0] or run_anterior
        if prima == run_anterior:
            continue   # vazut o singura data - probabil doar zgomot de paginare (vezi mai sus)
        cur.execute("""
            INSERT OR IGNORE INTO disparitii
                (model, categorie, sursa, ad_id, titlu, url, pret_eur,
                 prima_data, ultima_data, disparut_la, ore_pe_piata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (model_nume, categorie, sursa, ad_id, titlu, url, pret,
              prima, run_anterior, run_at, _ore_intre(prima, run_anterior)))
        n += cur.rowcount
    conn.commit()
    conn.close()
    return n


def acoperire():
    """(prima_scanare, ultima_scanare, numar_scanari) - cat de multa incredere
    sa ai in statistici_vanzari() / rata_vanzare()."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT MIN(run_at), MAX(run_at), COUNT(DISTINCT run_at) FROM model_prices")
    prima, ultima, n = cur.fetchone()
    conn.close()
    return prima, ultima, n or 0


def statistici_vanzari(model=None, categorie=None):
    """Pentru fiecare (model, tara): cate anunturi distincte am urmarit in cel
    putin 2 scanari diferite ("urmarite" - un anunt vazut o singura data n-a
    avut cum sa fie confirmat "disparut", vezi actualizeaza_disparitii), cate
    au disparut (probabil vandute/scoase) si cate ore au stat in medie pe
    piata inainte sa dispara. Folosit de verifica_vanzari.py si de mesajele
    de pe Telegram (rata_vanzare)."""
    conn = sqlite3.connect(DB_PATH)
    _creeaza_tabel_disparitii(conn)
    cur = conn.cursor()

    # model_prices nu are coloana "categorie" (doar tabelul disparitii) -
    # pentru categorie filtram dupa lista de modele din fisierele curente
    filtre_mp, param_mp = [], []
    if model:
        filtre_mp.append("model = ?")
        param_mp.append(model)
    if categorie:
        nume_modele = [m["nume"] for m in toate_modele(categorie)]
        if not nume_modele:
            conn.close()
            return []
        filtre_mp.append(f"model IN ({','.join('?' * len(nume_modele))})")
        param_mp.extend(nume_modele)
    unde_mp = (" AND " + " AND ".join(filtre_mp)) if filtre_mp else ""

    cur.execute(f"""
        SELECT model, sursa, COUNT(*) FROM (
            SELECT model, sursa, ad_id FROM model_prices
            WHERE 1=1{unde_mp}
            GROUP BY model, sursa, ad_id
            HAVING COUNT(DISTINCT run_at) >= 2
        ) GROUP BY model, sursa
    """, param_mp)
    urmarite = {(m, s): n for m, s, n in cur.fetchall()}

    filtre_d, param_d = [], []
    if model:
        filtre_d.append("model = ?")
        param_d.append(model)
    if categorie:
        filtre_d.append("categorie = ?")
        param_d.append(categorie)
    unde_d = (" AND " + " AND ".join(filtre_d)) if filtre_d else ""

    cur.execute(f"SELECT model, sursa, COUNT(*), AVG(ore_pe_piata) FROM disparitii WHERE 1=1{unde_d} GROUP BY model, sursa",
                param_d)
    rezultat = []
    for m, s, n_disp, ore_medii in cur.fetchall():
        n_urm = urmarite.get((m, s), n_disp)
        rezultat.append({
            "model": m, "sursa": s, "urmarite": n_urm, "disparute": n_disp,
            "rata_%": round(100 * n_disp / n_urm, 1) if n_urm else None,
            "ore_medii_pe_piata": round(ore_medii, 1) if ore_medii is not None else None,
        })
    conn.close()
    rezultat.sort(key=lambda r: r["disparute"], reverse=True)
    return rezultat


PRAG_ORE_ACOPERIRE_TELEGRAM = 48.0   # sub atat nu aratam rata_vanzare pe Telegram (prea putin istoric)


def rata_vanzare(model, sursa, minim_urmarite=5, minim_disparute=2):
    """Statistica pentru o singura pereche (model, tara), doar daca avem destule
    date sa fie relevanta: cel putin PRAG_ORE_ACOPERIRE_TELEGRAM ore de istoric
    in total (nu doar cateva scanari facute in aceeasi zi/sesiune - vezi
    verifica_vanzari.py) SI cel putin minim_urmarite anunturi urmarite /
    minim_disparute disparute pentru perechea asta. Intoarce None altfel.
    Folosit la formatarea mesajelor de chilipir pe Telegram
    (telegram_notify.format_chilipir) - pragul e mai strict decat in
    statistici_vanzari() ca sa nu trimitem pe Telegram cifre premature."""
    prima, ultima, n = acoperire()
    if n < 2 or _ore_intre(prima, ultima) < PRAG_ORE_ACOPERIRE_TELEGRAM:
        return None
    for r in statistici_vanzari(model):
        if r["sursa"] == sursa:
            if r["urmarite"] >= minim_urmarite and r["disparute"] >= minim_disparute:
                return r
            return None
    return None


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
    # ~o cerere Kleinanzeigen + pauza DELAY per model; 999.md adauga putin
    # (unele categorii au mai multe pagini pe Kleinanzeigen, de aici +1.5 s)
    return int(n_modele * (DELAY + 2.5)) // 60 + 1


def ruleaza(tinta=None):
    """Scaneaza modelele si intoarce (randuri, chilipiruri, run_at). Folosit de main() si de bot.py."""
    _CUVINTE_DISTINCTE_CACHE.clear()   # reciteste modele*.txt daca s-au editat de la scanarea trecuta
    _MODELE_CATEGORIE_CACHE.clear()
    modele = selecteaza(tinta)
    rates = scraper_999.get_rates()
    run_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    zona = f"{ORAS_DE} + {RAZA_KM} km" if RAZA_KM else "toata Germania"
    print(f"{len(modele)} modele | DE: {zona} | prag {PRAG_DIFERENTA}%")
    print(f"Durata estimata: ~{durata_estimata_min(len(modele))} min\n")

    # o conexiune refolosita per site, creata la fiecare scanare
    # (bot.py poate rula scanari din alte thread-uri)
    sesiune_de, sesiune_md = requests.Session(), requests.Session()
    descarcate_999 = pregateste_999(modele, rates, sesiune_md)

    randuri, toate_chilipiruri = [], []
    for i, m in enumerate(modele, 1):
        cat = CATEGORII[m["categorie"]]
        print(f"[{i}/{len(modele)}] {cat['emoji']} {m['nume']} ... ", end="", flush=True)
        try:
            de = curata(cauta_kleinanzeigen(cat, m["nume"], sesiune_de), m, cat)
            # fara pauza aici: 999.md e alt server, pauza DELAY de dupa model
            # le distanteaza oricum
            if m["categorie"] in descarcate_999:
                md = curata(descarcate_999[m["categorie"]], m, cat)
            else:
                md = curata(cauta_999(cat, m["nume"], rates, sesiune_md), m, cat)
        except Exception as e:
            print(f"EROARE: {e}")
            time.sleep(DELAY)
            continue
        rand, chil = analizeaza(m["nume"], m["categorie"], de, md)
        randuri.append(rand)
        toate_chilipiruri.extend(chil)
        salveaza_istoric(run_at, m["nume"], de + md)
        actualizeaza_disparitii(m["nume"], m["categorie"], run_at)
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
