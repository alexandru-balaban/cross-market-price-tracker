"""
Scraper pentru kleinanzeigen.de (cautari/categorii listate) - v2, actualizat
dupa redesign-ul site-ului (sept 2026).

IMPORTANT (citeste inainte sa rulezi):
- Kleinanzeigen interzice explicit scraping-ul automat in Termenii de
  Utilizare (§5), fara acord scris din partea lor. Acest script e un punct
  de plecare tehnic pentru uz personal, cu risc asumat - in principal
  blocare temporara de IP daca faci volum mare sau prea des.
- Foloseste delay mare intre cereri (3-5 secunde), nu rula prea des
  (o data la cateva ore e suficient - preturile nu se schimba in timp real),
  si tine volumul mic (cateva categorii/cautari, nu tot site-ul).
- Alternativa cu risc mai mic: foloseste "Suchauftrag speichern" (cautare
  salvata + alerta pe email) direct pe site, pentru anunturi noi.

Note tehnice (ce s-a schimbat fata de v1):
- Structura HTML e complet noua: fiecare anunt e un <article data-adid="..."
  data-href="...">, nu mai foloseste clasele vechi "aditem".
- Am reparat un bug clasic din requests: cand serverul nu trimite charset
  explicit in header-ul Content-Type, requests decodeaza gresit textul
  (ISO-8859-1 in loc de UTF-8), stricand caracterul € si literele germane
  (ü, ö). Solutia: dam bytes-urile brute (r.content) catre BeautifulSoup,
  care detecteaza encoding-ul corect singur.

Daca scriptul incepe sa returneze 0 rezultate din nou, structura s-a
schimbat iar - deschide pagina in Chrome, F12 > Elements, click pe un
anunt, si actualizeaza selectorii din scrape_page().
"""

import re
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


def parse_price(text):
    """'4.980 € VB' -> (4980.0, negociabil=True). '30 €' -> (30.0, False)."""
    if not text:
        return None, False
    negotiable = "VB" in text
    match = re.search(r"([\d.]+)\s*€", text)
    if not match:
        return None, negotiable
    price = float(match.group(1).replace(".", ""))
    return price, negotiable


def page_url(base_url, page):
    """Insereaza 'seite:N/' inainte de ultimul segment din URL (codul categorie+locatie)."""
    if page == 1:
        return base_url
    head, tail = base_url.rsplit("/", 1)
    return f"{head}/seite:{page}/{tail}"


def scrape_page(url):
    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    # Important: folosim r.content (bytes brute), nu r.text - BeautifulSoup
    # detecteaza encoding-ul corect singur, evitand bug-ul de mai sus.
    return parse_html(r.content)


def parse_html(content):
    """Extrage anunturile dintr-o pagina de rezultate (bytes sau text HTML).
    Separata de scrape_page ca s-o poata folosi si compara.py."""
    soup = BeautifulSoup(content, "html.parser")

    listings = []
    for ad in soup.select("article[data-adid]"):
        ad_id = ad.get("data-adid")
        href = ad.get("data-href")

        title_el = ad.select_one("h3 a")
        price_el = ad.select_one("p.font-strong.text-secondary")
        loc_icon = ad.select_one('svg[data-title="locationOutline"]')
        date_icon = ad.select_one('svg[data-title="calendarOutline"]')

        if not title_el or not href:
            continue

        price, negotiable = parse_price(price_el.get_text(strip=True) if price_el else "")
        location = loc_icon.parent.find("span").get_text(strip=True) if loc_icon else None
        posted = date_icon.parent.find("span").get_text(strip=True) if date_icon else None

        listings.append({
            "ad_id": ad_id,
            "title": title_el.get_text(strip=True),
            "url": "https://www.kleinanzeigen.de" + href if href.startswith("/") else href,
            "price": price,
            "negotiable": negotiable,
            "location": location,
            "posted": posted,
            "scraped_at": datetime.now().isoformat(),
        })
    return listings


def scrape_search(base_url, max_pages=5, delay=3):
    all_listings = []
    for page in range(1, max_pages + 1):
        url = page_url(base_url, page)
        print(f"Pagina {page}: {url}")
        listings = scrape_page(url)
        if not listings:
            print("  -> nicio pagina in plus, ma opresc.")
            break
        print(f"  -> {len(listings)} anunturi gasite")
        all_listings.extend(listings)
        time.sleep(delay)
    return all_listings


def save_to_db(listings, db_path="price_monitor.db"):
    conn = sqlite3.connect(db_path)
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
    for l in listings:
        conn.execute("""
            INSERT OR IGNORE INTO listings
            (source, ad_id, title, url, price, negotiable, location, posted, scraped_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("kleinanzeigen", l["ad_id"], l["title"], l["url"], l["price"],
              int(l["negotiable"]), l["location"], l["posted"], l["scraped_at"]))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    # Recomandare: ingusteaza cautarea (cuvant cheie / model) in loc de o
    # categorie intreaga - "foto in Munchen" are ~7000 anunturi si sute de
    # pagini, ceea ce inseamna mult trafic repetat daca monitorizezi des.
    URL = "https://www.kleinanzeigen.de/s-foto/muenchen/c245l6411"

    results = scrape_search(URL, max_pages=3, delay=3)
    print(f"\nAm gasit {len(results)} anunturi in total.")

    if results:
        print("\nExemplu (primele 3):")
        for l in results[:3]:
            print(f"  {l['title']} | {l['price']} EUR{' (VB)' if l['negotiable'] else ''} | {l['location']} | {l['posted']}")

    save_to_db(results)
    print("\nSalvat in price_monitor.db")
