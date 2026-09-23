"""
Scraper pentru 999.md - foloseste API-ul GraphQL intern al site-ului
(aceeasi cerere pe care o face pagina cand treci la pagina 2).

De ce API si nu HTML: 999.md e o aplicatie Next.js; datele vin ca JSON
curat (id, titlu, pret + moneda, stare, data), mult mai stabil decat
parsarea HTML-ului.

Preturile pe 999.md sunt fie in MDL, fie in EUR (cam jumatate-jumatate).
Scriptul le converteste pe toate in EUR folosind cursul oficial BNM
(Banca Nationala a Moldovei) din ziua respectiva, ca sa poata fi comparate
direct cu Kleinanzeigen.

Salveaza in aceeasi baza price_monitor.db ca scraperul Kleinanzeigen,
in tabelul "listings", cu source = "999".

Recomandari: nu rula prea des (o data la cateva ore e suficient), pastreaza
delay-ul intre cereri.
"""

import json
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

GRAPHQL_URL = "https://999.md/graphql"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "*/*",
    "lang": "ro",
    "source": "desktop",
    "Origin": "https://999.md",
    "Referer": "https://999.md/ro/list/audio-video-photo/digital-cameras",
}

# Subcategorii 999.md (id-ul se vede in cererea SearchAds, "subCategoryId").
# 77 = Audio-Video-Foto > Aparate de fotografiat
SUBCATEGORY_ID = 77

PAGE_SIZE = 78          # cat foloseste si site-ul
FALLBACK_EUR_MDL = 20.0  # folosit doar daca BNM nu raspunde

QUERY = """
query SearchAds($input: Ads_SearchInput!) {
  searchAds(input: $input) {
    count
    ads {
      id
      title
      price: feature(id: 2) { value }
      offerType: feature(id: 1) { value }
      condition: feature(id: 593) { value }
      author: feature(id: 795) { value }
      reseted(input: {format: "2006-01-02 15:04", locale: ro_RO, timezone: "Europe/Chisinau", getDiff: false})
    }
  }
}
"""


def get_rates():
    """Curs oficial BNM: cati MDL pentru 1 unitate din fiecare moneda."""
    today = datetime.now().strftime("%d.%m.%Y")
    url = f"https://www.bnm.md/en/official_exchange_rates?get_xml=1&date={today}"
    try:
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        rates = {"MDL": 1.0}
        for v in root.findall("Valute"):
            code = v.findtext("CharCode")
            nominal = float(v.findtext("Nominal"))
            value = float(v.findtext("Value"))
            rates[code] = value / nominal
        print(f"Curs BNM {today}: 1 EUR = {rates['EUR']:.4f} MDL")
        return rates
    except Exception as e:
        print(f"ATENTIE: nu am putut lua cursul BNM ({e}). Folosesc 1 EUR = {FALLBACK_EUR_MDL} MDL.")
        return {"MDL": 1.0, "EUR": FALLBACK_EUR_MDL}


def to_eur(amount, currency, rates):
    if amount is None or currency not in rates:
        return None
    mdl = amount * rates[currency]
    return round(mdl / rates["EUR"], 2)


def fetch_page(skip):
    payload = {
        "operationName": "SearchAds",
        "query": QUERY,
        "variables": {
            "input": {
                "source": "AD_SOURCE_DESKTOP_REDESIGN",
                "sort": "SORT_ADS_DATE_DESC",
                "pagination": {"limit": PAGE_SIZE, "skip": skip},
                "filters": [],
                "subCategoryId": SUBCATEGORY_ID,
            }
        },
    }
    r = requests.post(GRAPHQL_URL, headers=HEADERS, data=json.dumps(payload), timeout=20)
    r.raise_for_status()
    data = r.json()
    if "errors" in data:
        raise RuntimeError(f"Eroare GraphQL: {data['errors']}")
    return data["data"]["searchAds"]


def parse_ad(ad, rates):
    offer = (ad.get("offerType") or {}).get("value") or {}
    if offer.get("translated") == "Cumpăr":
        return None  # cereri de cumparare, nu oferte

    price_val = (ad.get("price") or {}).get("value") or {}
    amount = price_val.get("value")
    unit = price_val.get("unit") or ""           # ex. "UNIT_MDL", "UNIT_EUR"
    currency = unit.replace("UNIT_", "") or None
    if not amount:
        amount = None

    condition = ((ad.get("condition") or {}).get("value") or {}).get("translated")
    author = ((ad.get("author") or {}).get("value") or {}).get("translated")

    return {
        "ad_id": str(ad["id"]),
        "title": (ad.get("title") or "").strip(),
        "url": f"https://999.md/ro/{ad['id']}",
        "price_original": amount,
        "currency": currency,
        "price": to_eur(amount, currency, rates),   # in EUR
        "negotiable": bool(price_val.get("bargain")),
        "condition": condition,
        "location": author,  # 999 nu da regiunea in lista; tinem "Persoana fizica"/"Magazin"
        "posted": ad.get("reseted"),
        "scraped_at": datetime.now().isoformat(),
    }


def scrape(max_pages=3, delay=3):
    rates = get_rates()
    results = []
    for page in range(max_pages):
        skip = page * PAGE_SIZE
        print(f"Pagina {page + 1} (skip={skip})")
        block = fetch_page(skip)
        ads = block.get("ads") or []
        if page == 0:
            print(f"  total anunturi in categorie: {block.get('count')}")
        if not ads:
            print("  -> nicio pagina in plus, ma opresc.")
            break
        parsed = [p for p in (parse_ad(a, rates) for a in ads) if p]
        print(f"  -> {len(parsed)} oferte (din {len(ads)} anunturi)")
        results.extend(parsed)
        time.sleep(delay)
    return results


def ensure_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS listings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            ad_id TEXT,
            title TEXT,
            url TEXT UNIQUE,
            price REAL,
            negotiable INTEGER,
            location TEXT,
            posted TEXT,
            scraped_at TEXT
        )
    """)
    # coloane in plus pentru 999 (pret original + moneda + stare);
    # le adaugam doar daca lipsesc, ca sa nu stricam tabelul existent
    existing = {row[1] for row in conn.execute("PRAGMA table_info(listings)")}
    for col, typ in [("price_original", "REAL"), ("currency", "TEXT"), ("condition", "TEXT")]:
        if col not in existing:
            conn.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")


def save_to_db(listings, db_path="price_monitor.db"):
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    for l in listings:
        # daca anuntul exista deja, ii actualizam pretul (pretul se poate schimba)
        conn.execute("""
            INSERT INTO listings
            (source, ad_id, title, url, price, negotiable, location, posted, scraped_at,
             price_original, currency, condition)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                title = excluded.title,
                price = excluded.price,
                price_original = excluded.price_original,
                currency = excluded.currency,
                scraped_at = excluded.scraped_at
        """, ("999", l["ad_id"], l["title"], l["url"], l["price"], int(l["negotiable"]),
              l["location"], l["posted"], l["scraped_at"],
              l["price_original"], l["currency"], l["condition"]))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    results = scrape(max_pages=3, delay=3)
    print(f"\nAm gasit {len(results)} oferte in total.")

    if results:
        print("\nExemplu (primele 5):")
        for l in results[:5]:
            orig = f"{l['price_original']:g} {l['currency']}" if l["price_original"] else "fara pret"
            eur = f"{l['price']:.0f} EUR" if l["price"] is not None else "-"
            print(f"  {l['title']} | {orig} = {eur} | {l['condition']} | {l['posted']}")

    save_to_db(results)
    print("\nSalvat in price_monitor.db")
