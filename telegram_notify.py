"""
telegram_notify.py - trimite rezultatele lui compara.py pe Telegram.

E folosit automat de compara.py (la finalul scanarii) si de bot.py.
Nu trebuie rulat direct, in afara de pasii de configurare de mai jos.

CONFIGURARE (o singura data):
  1. In Telegram, scrie-i lui @BotFather: /newbot -> alegi un nume
     -> primesti un token de forma 123456789:AAH...
  2. Deschide fisierul .env din folderul proiectului (in Terminal:
     open -e .env) si pune tokenul dupa TELEGRAM_BOT_TOKEN=
  3. Deschide botul tau in Telegram si apasa Start (sau scrie-i orice).
  4. python3 telegram_notify.py --get-chat-id
     -> copiezi numarul afisat in .env dupa TELEGRAM_CHAT_ID=
  5. python3 telegram_notify.py --test
     -> trebuie sa primesti in Telegram un mesaj de proba.
"""

import html
import json
import os
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
SEEN_FILE = BASE_DIR / "trimise_telegram.json"

# Cate anunturi individuale trimitem maxim dupa o scanare (restul apar doar
# ca numar in rezumat), ca sa nu primesti 60 de notificari deodata.
MAX_CHILIPIRURI_PE_SCANARE = 15
# Dupa cate zile un anunt deja trimis poate fi trimis din nou (daca e tot activ)
UITA_DUPA_ZILE = 30

API_URL = "https://api.telegram.org/bot{token}/{method}"


class NeconfiguratError(Exception):
    """Lipseste tokenul sau chat id-ul din .env."""


# ============================================================
#  configurare
# ============================================================

def _load_env():
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


def _cfg(name):
    _load_env()
    value = os.environ.get(name, "").strip()
    if not value:
        raise NeconfiguratError(f"lipseste {name} in {ENV_FILE.name} (vezi pasii din telegram_notify.py)")
    return value


def token():
    return _cfg("TELEGRAM_BOT_TOKEN")


def chat_id():
    return _cfg("TELEGRAM_CHAT_ID")


# ============================================================
#  Telegram API
# ============================================================

def _call(method, http_timeout=20, **params):
    url = API_URL.format(token=token(), method=method)
    for incercare in range(3):
        try:
            resp = requests.post(url, json=params, timeout=http_timeout)
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            if incercare == 2:
                raise RuntimeError(f"Telegram nu raspunde ({method}): {e}")
            time.sleep(3)
            continue
        if data.get("ok"):
            return data["result"]
        if data.get("error_code") == 429:  # prea multe mesaje -> asteptam cat cere Telegram
            time.sleep(data.get("parameters", {}).get("retry_after", 5) + 1)
            continue
        if data.get("error_code") == 401:
            raise NeconfiguratError("tokenul din .env e gresit (Telegram raspunde 401 Unauthorized)")
        raise RuntimeError(f"Telegram a refuzat {method}: {data.get('description')}")
    raise RuntimeError(f"Telegram: {method} a esuat dupa 3 incercari")


def send_message(text, chat=None, url_buton=None, text_buton="Deschide anunțul", butoane=None):
    """Trimite un mesaj (HTML). Mesajele lungi se impart automat pe linii.
    butoane = randuri de butoane [(text, cod), ...]; cand apesi unul, botul primeste "cod"."""
    bucati = _imparte(text, 4000)
    for i, bucata in enumerate(bucati):
        params = dict(
            chat_id=chat or chat_id(),
            text=bucata,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
        if url_buton and i == len(bucati) - 1:
            params["reply_markup"] = {"inline_keyboard": [[{"text": text_buton, "url": url_buton}]]}
        if butoane and i == len(bucati) - 1:
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": t, "callback_data": cod} for t, cod in rand] for rand in butoane]}
        _call("sendMessage", **params)


def raspunde_buton(callback_id, text=None):
    """Confirma apasarea unui buton (altfel Telegram arata un cerc de incarcare)."""
    params = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    _call("answerCallbackQuery", **params)


def editeaza_mesaj(chat, message_id, text):
    """Inlocuieste textul unui mesaj si ii scoate butoanele."""
    _call("editMessageText", chat_id=chat, message_id=message_id, text=text, parse_mode="HTML")


def _imparte(text, limita):
    if len(text) <= limita:
        return [text]
    bucati, curent = [], ""
    for linie in text.split("\n"):
        if len(curent) + len(linie) + 1 > limita and curent:
            bucati.append(curent)
            curent = ""
        curent += (linie if not curent else "\n" + linie)[:limita]
    if curent:
        bucati.append(curent)
    return bucati


def get_updates(offset=None, timeout=50):
    params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    return _call("getUpdates", http_timeout=timeout + 15, **params)


def set_commands(comenzi):
    _call("setMyCommands", commands=[{"command": c, "description": d} for c, d in comenzi])


# ============================================================
#  formatare mesaje
# ============================================================

def _e(x):
    return html.escape(str(x))


def _emoji(cheie):
    try:
        from compara import CATEGORII
        return CATEGORII[cheie]["emoji"]
    except Exception:
        return "•"


def _directie_scurta(directie):
    return "DE→MD" if directie.startswith("cumpari DE") else "MD→DE"


def _linie_model(r):
    if r.get("diferenta_%") is None:
        return f"{_emoji(r.get('categorie'))} <b>{_e(r['model'])}</b>: {r['semnal'] or 'date insuficiente'} (DE {r['n_DE']}, MD {r['n_MD']} anunțuri)"
    if r["directie"].startswith("cumpari DE"):
        ieftin, scump = r["mediana_DE"], r["mediana_MD"]
    else:
        ieftin, scump = r["mediana_MD"], r["mediana_DE"]
    semn = " ⭐" if "OPORTUNITATE" in r["semnal"] else (" (puține anunțuri)" if r["semnal"] == "putine anunturi" else "")
    return (f"{_emoji(r.get('categorie'))} <b>{_e(r['model'])}</b> {_directie_scurta(r['directie'])}: "
            f"{ieftin} € → {scump} € (+{r['diferenta_%']:g}%, profit ~{r['profit_estimat_EUR']} €){semn}")


def format_rezumat(randuri, run_at, n_noi, n_total):
    comparabile = sorted((r for r in randuri if r.get("diferenta_%") is not None),
                         key=lambda r: r["diferenta_%"], reverse=True)
    oportunitati = [r for r in comparabile if "OPORTUNITATE" in r["semnal"]]

    n = len(randuri)
    lines = [f"🔎 <b>Scanare {_e(run_at)}</b> · {n} {'model' if n == 1 else 'modele'}"]

    if len(randuri) <= 5:
        # scanare pe un singur model / cateva: aratam tot
        lines.append("")
        lines += [_linie_model(r) for r in randuri]
    elif oportunitati:
        lines.append("\n⭐ <b>Oportunități (după mediane)</b>")
        lines += [_linie_model(r) for r in oportunitati[:10]]
        if len(oportunitati) > 10:
            lines.append(f"… și încă {len(oportunitati) - 10}")
    else:
        lines.append("\nNicio oportunitate la nivel de model.")
        if comparabile:
            lines.append("<b>Cele mai mari diferențe:</b>")
            lines += [_linie_model(r) for r in comparabile[:5]]

    if n_total == 0:
        lines.append("\n📌 Niciun anunț individual sub prețul pieței.")
    elif n_noi == 0:
        lines.append(f"\n📌 {n_total} anunțuri sub prețul pieței, toate trimise deja anterior.")
    else:
        extra = f", îți trimit top {MAX_CHILIPIRURI_PE_SCANARE}" if n_noi > MAX_CHILIPIRURI_PE_SCANARE else ""
        deja = f" ({n_total - n_noi} deja trimise)" if n_total > n_noi else ""
        lines.append(f"\n📌 <b>{n_noi}</b> anunțuri noi sub prețul pieței{deja}{extra}:")
    return "\n".join(lines)


def format_chilipir(c):
    if c["tara"] == "DE":
        steag, alta = "🇩🇪 → 🇲🇩", "MD"
    else:
        steag, alta = "🇲🇩 → 🇩🇪", "DE"
    pret = f"{c['pret_EUR']} €"
    if not str(c.get("pret_original", "")).endswith("EUR"):
        pret += f" ({_e(c['pret_original'])})"
    return "\n".join([
        f"{steag} {_emoji(c.get('categorie'))} <b>{_e(c['model'])}</b>",
        _e(c["titlu"]),
        f"💶 {pret}",
        f"📊 Mediana {alta}: {c['mediana_tara_cealalta']} € · cu {c['sub_mediana_%']:g}% sub",
        f"💰 Profit estimat: <b>~{c['profit_estimat_EUR']} €</b>",
    ])


# ============================================================
#  anunturi deja trimise (ca sa nu primesti aceleasi de fiecare data)
# ============================================================

def _cheie(c):
    # acelasi anunt cu pret schimbat = anunt nou
    return f"{c['url']}|{c['pret_EUR']}"


def _load_seen():
    try:
        data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    limita = time.time() - UITA_DUPA_ZILE * 86400
    return {k: t for k, t in data.items() if t >= limita}


def _save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, ensure_ascii=False, indent=0), encoding="utf-8")


# ============================================================
#  functia principala
# ============================================================

def trimite_rezultate(randuri, chilipiruri, run_at, doar_noi=True, chat=None):
    """Trimite rezumatul + anunturile sub pretul pietei (cele mai profitabile primele).
    doar_noi=True: sare peste anunturile trimise la scanarile anterioare.
    Intoarce cate anunturi individuale a trimis."""
    chat = chat or chat_id()
    seen = _load_seen()
    chilipiruri = sorted(chilipiruri, key=lambda c: c["profit_estimat_EUR"], reverse=True)
    noi = [c for c in chilipiruri if not doar_noi or _cheie(c) not in seen]

    send_message(format_rezumat(randuri, run_at, len(noi), len(chilipiruri)), chat)

    acum = time.time()
    for c in noi[:MAX_CHILIPIRURI_PE_SCANARE]:
        send_message(format_chilipir(c), chat, url_buton=c["url"])
        time.sleep(1.1)  # sub limita Telegram de ~1 mesaj/secunda intr-un chat
    # le marcam pe toate ca trimise (si pe cele peste limita), ca sa nu revina la fiecare scanare
    for c in noi:
        seen[_cheie(c)] = acum
    _save_seen(seen)
    return min(len(noi), MAX_CHILIPIRURI_PE_SCANARE)


# ============================================================
#  configurare din linia de comanda
# ============================================================

def _print_chat_ids():
    updates = _call("getUpdates")
    gasite = {}
    for u in updates:
        msg = u.get("message") or u.get("channel_post") or {}
        chat = msg.get("chat")
        if chat:
            gasite[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not gasite:
        print("Nu vad niciun mesaj. Deschide botul in Telegram, apasa Start si ruleaza din nou.")
        return
    for cid, nume in gasite.items():
        print(f"TELEGRAM_CHAT_ID={cid}    ({nume})")


if __name__ == "__main__":
    try:
        if "--get-chat-id" in sys.argv:
            _print_chat_ids()
        elif "--test" in sys.argv:
            send_message(format_chilipir({
                "model": "Test", "tara": "DE", "titlu": "Mesaj de proba - Sony A7 III body",
                "pret_EUR": 700, "pret_original": "700 EUR", "mediana_tara_cealalta": 875,
                "sub_mediana_%": 20.0, "profit_estimat_EUR": 135,
                "url": "https://www.kleinanzeigen.de",
            }), url_buton="https://www.kleinanzeigen.de")
            print("Mesaj de test trimis - verifica Telegram.")
        else:
            print(__doc__)
    except NeconfiguratError as e:
        print(f"Telegram nu e configurat: {e}")
