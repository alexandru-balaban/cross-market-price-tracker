"""
rezumat.py - citeste pagina unui anunt Kleinanzeigen (folosit de watchlist.py):
descrierea, detaliile (Zustand, Größe, Marke...) si daca vanzatorul trimite prin posta.
Fara traducere / AI - descrierea ramane in germana.
"""

import re

import requests
from bs4 import BeautifulSoup

import scraper_kleinanzeigen

LUNGIME_EXTRAS = 300   # cate caractere din descriere apar in mesajul de Telegram


def detalii_anunt(url):
    """{"descriere": str, "detalii": {"Größe": "42", "Zustand": "Gut", ...}, "versand": bool|None}"""
    r = requests.get(url, headers=scraper_kleinanzeigen.HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.content, "html.parser")

    desc_el = soup.select_one("#viewad-description-text") or soup.select_one('[itemprop="description"]')
    descriere = desc_el.get_text("\n", strip=True) if desc_el else ""

    detalii = {}
    for li in soup.select("#viewad-details li"):
        parti = [t.strip() for t in li.stripped_strings]
        if len(parti) >= 2:
            detalii[parti[0]] = " ".join(parti[1:])
        elif len(parti) == 1:
            m = re.match(r"(Art|Marke|Größe|Farbe|Zustand|Material|Versand)\s*(.+)", parti[0])
            if m:
                detalii[m.group(1)] = m.group(2)

    text = r.content.decode("utf-8", "ignore")
    versand = None
    if "Nur Abholung" in text:
        versand = False
    elif re.search(r"Versand ab|Versand möglich", text):
        versand = True
    return {"descriere": descriere, "detalii": detalii, "versand": versand}


def extras(descriere, limita=LUNGIME_EXTRAS):
    """Inceputul descrierii, taiat frumos la sfarsit de propozitie sau la spatiu."""
    text = re.sub(r"\s+", " ", descriere or "").strip()
    if len(text) <= limita:
        return text
    taiat = text[:limita]
    m = re.search(r"^(.*[.!?])\s", taiat)
    if m and len(m.group(1)) > limita * 0.5:
        return m.group(1) + " …"
    return taiat.rsplit(" ", 1)[0] + " …"
