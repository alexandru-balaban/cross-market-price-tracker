"""
Script de diagnostic - salveaza raspunsul brut de la kleinanzeigen.de
ca sa vedem de ce scraperul principal a gasit 0 anunturi.
"""
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

URL = "https://www.kleinanzeigen.de/s-foto/muenchen/c245l6411"

r = requests.get(URL, headers=HEADERS, timeout=15)

print(f"Status code: {r.status_code}")
print(f"Lungime raspuns: {len(r.text)} caractere")
print(f"Content-Type: {r.headers.get('Content-Type')}")
print()

# cateva verificari utile
checks = ["aditem", "captcha", "Bitte bestätige", "cookie", "consent", "access denied", "blocked"]
for c in checks:
    count = r.text.lower().count(c.lower())
    print(f"  apare '{c}': {count} ori")

with open("raspuns_debug.html", "w", encoding="utf-8") as f:
    f.write(r.text)

print()
print("Am salvat tot raspunsul in raspuns_debug.html - deschide-l in browser sau cu un editor de text.")
