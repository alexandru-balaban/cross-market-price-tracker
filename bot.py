"""
bot.py - botul de Telegram pentru ax_resellDEMD.

Porneste-l din folderul proiectului:
    caffeinate -i /usr/bin/python3 bot.py
(caffeinate = Mac-ul nu adoarme cat ruleaza botul)

Cat timp ruleaza (terminalul ramane deschis, Ctrl+C il opreste):
  - scaneaza singur toate categoriile la fiecare SCANARE_AUTOMATA_ORE ore
    si iti trimite doar anunturile noi;
  - raspunde la comenzi in Telegram:
        /scan               scanare completa acum (toate categoriile)
        /scan casti         doar o categorie (camere / casti / console / controlere)
        /model Sony A7 III  un singur model (~10 secunde)
        /modele             modelele monitorizate, pe categorii
        /status             ce face botul, cand e urmatoarea scanare
        /meniu              butoanele de alegere a scanarii
        /watch              verifica acum watchlist-ul personal (watchlist.txt)
        /watch Nike Pegasus un singur produs (din lista sau oricare: "/watch Garmin 265 ; max 250")
        /watchlist          ce e in watchlist
        /adauga Garmin Forerunner 265 ; max 250    adauga in watchlist
        /sterge Garmin Forerunner 265              scoate din watchlist
    Poti scrie si direct numele modelului sau al categoriei.
  - verifica watchlist-ul personal la fiecare WATCHLIST_MINUTE minute.
  - la pornire iti trimite un mesaj cu butoane: scanare completa sau o categorie.

Raspunde doar in chat-ul din TELEGRAM_CHAT_ID (.env); ceilalti sunt ignorati.
Configurarea tokenului: vezi telegram_notify.py.
"""

import html
import os
import threading
import time
import traceback
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)  # compara.py foloseste cai relative (fisierele de modele, price_monitor.db)

import compara  # noqa: E402
import telegram_notify as tg  # noqa: E402
import watchlist  # noqa: E402

# ============================================================
#  SETARI
# ============================================================
SCANARE_AUTOMATA_ORE = 6        # 0 = fara scanari automate, doar la comanda
SCANARE_LA_PORNIRE = False      # True = o scanare completa imediat ce pornesti botul
WATCHLIST_MINUTE = 30           # cat de des verifica watchlist-ul personal (0 = doar la /watch)
# ============================================================

COMENZI = [
    ("meniu", "Alege ce să scanez (butoane)"),
    ("scan", "Scanare completă (sau: /scan casti)"),
    ("model", "Un singur model, ex: /model Sony A7 III"),
    ("modele", "Modelele monitorizate, pe categorii"),
    ("watch", "Watchlist acum (sau: /watch Nike Pegasus)"),
    ("watchlist", "Ce e în watchlist"),
    ("adauga", "Adaugă în watchlist: Nume ; max 120"),
    ("sterge", "Scoate din watchlist: Nume"),
    ("status", "Starea botului"),
    ("help", "Ajutor"),
]


def ajutor():
    categorii = ", ".join(compara.CATEGORII)
    return (
        "🤖 <b>ax_resellDEMD</b>\n\n"
        "/scan — scanare completă acum\n"
        f"/scan casti — doar o categorie ({categorii})\n"
        "/model Sony A7 III — un singur model\n"
        "/modele — modelele monitorizate, pe categorii\n"
        "/status — ce face botul\n"
        "/meniu — butoanele de alegere a scanării\n\n"
        "👀 <b>Watchlist personal</b> (Kleinanzeigen, toate categoriile, München + raza)\n"
        "/watch — verifică acum toată lista\n"
        "/watch Nike Pegasus — doar un produs (toate anunțurile active sub preț)\n"
        "<code>/watch Garmin Forerunner 265 ; max 250</code> — căutare de o dată, fără s-o adaugi\n"
        "/watchlist — ce urmăresc\n"
        "<code>/adauga Garmin Forerunner 265 ; max 250</code>\n"
        "<code>/adauga ASICS Novablast ; max 90 ; marime 44</code>\n"
        "/sterge Garmin Forerunner 265\n\n"
        "Poți scrie și direct numele modelului sau al categoriei."
    )


scanare_lock = threading.Lock()
stare = {"curenta": None, "ultima": None, "urmatoarea": None}


def modele():
    return [m["nume"] for m in compara.toate_modele()]


def butoane_meniu():
    """Un buton pentru scanarea completa + cate unul pentru fiecare categorie (cate 2 pe rand)."""
    n_total = len(modele())
    randuri = [[(f"🔎 Scanare completă · {n_total} modele · ~{compara.durata_estimata_min(n_total)} min", "scan:toate")]]
    rand = []
    for cheie, cat in compara.CATEGORII.items():
        n = len(compara.toate_modele(cheie))
        rand.append((f"{cat['emoji']} {cat['nume']} ({n})", f"scan:{cheie}"))
        if len(rand) == 2:
            randuri.append(rand)
            rand = []
    if rand:
        randuri.append(rand)
    n_watch = len(watchlist.citeste())
    randuri.append([(f"👀 Verifică watchlist ({n_watch})", "watch:acum")])
    randuri.append([("⏸ Nimic acum", "scan:nimic")])
    return randuri


def trimite_meniu(chat, text="Ce scanez?"):
    tg.send_message(text, chat, butoane=butoane_meniu())


def trateaza_buton(cq, chat_autorizat):
    """Apasare pe un buton din meniu."""
    msg = cq.get("message") or {}
    chat = str(msg.get("chat", {}).get("id", ""))
    if chat != chat_autorizat:
        tg.raspunde_buton(cq["id"], "Bot privat.")
        return
    data = cq.get("data") or ""
    if data.startswith("watch:"):
        tg.raspunde_buton(cq["id"], "Verific watchlist-ul…")
        tg.editeaza_mesaj(chat, msg["message_id"], "✅ Ales: <b>👀 Watchlist</b>")
        porneste_watchlist(chat, manual=True)
        return
    cod = data.split(":", 1)[-1]
    if cod == "nimic":
        tg.raspunde_buton(cq["id"], "Ok, aștept comenzi.")
        tg.editeaza_mesaj(chat, msg["message_id"], "⏸ Nicio scanare acum. /meniu când vrei să alegi.")
        return
    tinta = None if cod == "toate" else cod
    if tinta is not None and tinta not in compara.CATEGORII:
        tg.raspunde_buton(cq["id"], "Categorie necunoscută.")
        return
    ales = "Scanare completă" if tinta is None else f"{compara.CATEGORII[tinta]['emoji']} {compara.CATEGORII[tinta]['nume']}"
    tg.raspunde_buton(cq["id"], f"Pornesc: {ales}")
    # scoatem butoanele ca sa nu pornesti aceeasi scanare de doua ori
    tg.editeaza_mesaj(chat, msg["message_id"], f"✅ Ales: <b>{ales}</b>")
    porneste_scanare(chat, tinta)


watch_lock = threading.Lock()


def porneste_watchlist(chat, manual=False):
    """Verifica watchlist-ul intr-un fir separat (dureaza ~4 s per produs)."""
    if not watch_lock.acquire(blocking=False):
        if manual:
            tg.send_message("⏳ Verific deja watchlist-ul.", chat)
        return

    def job():
        try:
            watchlist.trimite(chat, arata_gol=manual)
        except Exception as e:
            traceback.print_exc()
            if manual:
                tg.send_message(f"⚠️ Watchlist: {html.escape(str(e))[:500]}", chat)
        finally:
            watch_lock.release()

    threading.Thread(target=job, daemon=True).start()


def porneste_watch_unul(chat, text):
    def job():
        try:
            watchlist.trimite_unul(text, chat)
        except Exception as e:
            traceback.print_exc()
            tg.send_message(f"⚠️ Watchlist: {html.escape(str(e))[:500]}", chat)

    threading.Thread(target=job, daemon=True).start()


def lista_watchlist():
    produse = watchlist.citeste()
    if not produse:
        return "👀 Watchlist-ul e gol.\nAdaugă: <code>/adauga Garmin Forerunner 265 ; max 250</code>"
    linii = [f"👀 <b>Watchlist</b> — {len(produse)} produse (watchlist.txt)"]
    for p in produse:
        marime = f", mărime {p['marime']}" if p["marime"] else ""
        linii.append(f"• {html.escape(p['nume'])} — max {p['max']:g} €{marime}")
    return "\n".join(linii)


def gaseste_tinta(text):
    """'casti' -> categoria; 'sony a7iii' -> 'Sony A7 III'; altfel None."""
    t = text.strip().lower()
    if t in compara.CATEGORII:
        return t
    norm = lambda s: "".join(s.lower().split())
    for nume in modele():
        if norm(nume) == norm(text):
            return nume
    return None


def porneste_scanare(chat, tinta=None, automata=False):
    """tinta: None = tot, cheia unei categorii, sau numele unui model."""
    if not scanare_lock.acquire(blocking=False):
        tg.send_message(f"⏳ Rulează deja o scanare ({stare['curenta']}). Îți trimit rezultatele când se termină.", chat)
        return

    un_model = tinta is not None and tinta not in compara.CATEGORII

    def job():
        try:
            if un_model:
                stare["curenta"] = tinta
                tg.send_message(f"▶️ Scanez <b>{html.escape(tinta)}</b>…", chat)
            else:
                n = len(compara.selecteaza(tinta))
                if tinta:
                    cat = compara.CATEGORII[tinta]
                    ce = f"{cat['emoji']} {cat['nume']}"
                else:
                    ce = "toate categoriile"
                stare["curenta"] = f"{ce}, {n} modele"
                eticheta = "Scanare automată" if automata else "Pornesc scanarea"
                tg.send_message(f"▶️ {eticheta}: {ce}, {n} modele, ~{compara.durata_estimata_min(n)} min.", chat)

            randuri, chilipiruri, run_at = compara.ruleaza(tinta)
            # la un singur model cerut explicit arata tot, chiar daca a mai fost trimis
            tg.trimite_rezultate(randuri, chilipiruri, run_at, doar_noi=not un_model, chat=chat)
            if tinta is None:
                stare["ultima"] = run_at
        except Exception as e:
            traceback.print_exc()
            tg.send_message(f"⚠️ Scanarea a eșuat: {html.escape(str(e))[:500]}", chat)
        finally:
            stare["curenta"] = None
            scanare_lock.release()

    threading.Thread(target=job, daemon=True).start()


def trateaza(text, chat):
    primul, _, rest = text.partition(" ")
    cmd = primul.split("@")[0].lower() if primul.startswith("/") else None
    arg = rest.strip()

    if cmd in ("/start", "/help"):
        tg.send_message(ajutor(), chat)
        trimite_meniu(chat)

    elif cmd == "/meniu":
        trimite_meniu(chat)

    elif cmd == "/watch":
        if arg:
            porneste_watch_unul(chat, arg)
        else:
            porneste_watchlist(chat, manual=True)

    elif cmd == "/watchlist":
        tg.send_message(lista_watchlist(), chat)

    elif cmd == "/adauga":
        if not arg:
            tg.send_message("Scrie produsul și prețul maxim, ex:\n<code>/adauga Garmin Forerunner 265 ; max 250</code>\n"
                            "Opțional: <code>; marime 44</code>, sau variante: "
                            "<code>/adauga Nike Pegasus = pegasus, -trail ; max 80</code>", chat)
            return
        p = watchlist.adauga(arg)
        if p:
            marime = f", mărime {p['marime']}" if p["marime"] else ""
            tg.send_message(f"✅ Adăugat: <b>{html.escape(p['nume'])}</b> — max {p['max']:g} €{marime}. Verific acum…", chat)
            porneste_watchlist(chat, manual=True)
        else:
            tg.send_message("❓ Lipsește prețul maxim. Ex: <code>/adauga Garmin Forerunner 265 ; max 250</code>", chat)

    elif cmd == "/sterge":
        if arg and watchlist.sterge(arg):
            tg.send_message(f"🗑 Am scos „{html.escape(arg)}” din watchlist (rândul e dezactivat cu # în watchlist.txt).", chat)
        else:
            tg.send_message(f"❓ Nu găsesc „{html.escape(arg)}” în watchlist.\n\n" + lista_watchlist(), chat)

    elif cmd == "/scan":
        if arg and arg.lower() not in compara.CATEGORII:
            tg.send_message(f"❓ Categorii: {', '.join(compara.CATEGORII)}. Ex: <code>/scan casti</code>", chat)
        else:
            porneste_scanare(chat, arg.lower() or None)

    elif cmd == "/modele":
        bucati = []
        for cheie, cat in compara.CATEGORII.items():
            if arg and arg.lower() != cheie:
                continue
            nume = [m["nume"] for m in compara.toate_modele(cheie)]
            bucati.append(f"{cat['emoji']} <b>{cat['nume']}</b> — {len(nume)} modele "
                          f"({cat['fisier_modele']}) · /scan {cheie}\n"
                          + ", ".join(html.escape(n) for n in nume))
        tg.send_message("\n\n".join(bucati) or "Nu există categoria asta.", chat)

    elif cmd == "/status":
        lines = [f"🟢 Botul rulează · {len(modele())} modele în {len(compara.CATEGORII)} categorii"]
        zona = f"{compara.ORAS_DE} + {compara.RAZA_KM} km" if compara.RAZA_KM else "toată Germania"
        lines.append(f"Germania: {zona}")
        lines.append(f"Scanare în curs: {stare['curenta']}" if stare["curenta"] else "Nicio scanare în curs.")
        lines.append(f"Ultima scanare completă: {stare['ultima'] or '—'}")
        lines.append(f"Watchlist: {len(watchlist.citeste())} produse, verificat "
                     + (f"la fiecare {WATCHLIST_MINUTE} min" if WATCHLIST_MINUTE else "doar la /watch"))
        if stare["urmatoarea"]:
            lines.append(f"Următoarea automată: {stare['urmatoarea'].strftime('%d.%m %H:%M')}")
        else:
            lines.append("Scanări automate: dezactivate")
        tg.send_message("\n".join(lines), chat)

    elif cmd == "/model" or cmd is None:
        cerut = arg if cmd else text
        if not cerut:
            tg.send_message("Scrie și modelul, ex: <code>/model Sony A7 III</code>", chat)
            return
        tinta = gaseste_tinta(cerut)
        if tinta:
            porneste_scanare(chat, tinta)
        else:
            tg.send_message(f"❓ Nu găsesc „{html.escape(cerut)}” în listele de modele. Vezi /modele.", chat)

    else:
        tg.send_message("Nu cunosc comanda asta. /help", chat)


def main():
    try:
        chat_autorizat = str(tg.chat_id())
        tg.token()
        # ignora mesajele trimise cat botul era oprit
        vechi = tg.get_updates(offset=-1, timeout=0)
        offset = vechi[-1]["update_id"] + 1 if vechi else None
        tg.set_commands(COMENZI)
    except tg.NeconfiguratError as e:
        print(f"Telegram nu e configurat: {e}")
        return

    interval = SCANARE_AUTOMATA_ORE * 3600
    urmatoarea = time.time() + (0 if SCANARE_LA_PORNIRE else interval) if interval else None

    def seteaza_urmatoarea(ts):
        stare["urmatoarea"] = datetime.fromtimestamp(ts) if ts else None

    seteaza_urmatoarea(urmatoarea)
    interval_watch = WATCHLIST_MINUTE * 60
    urmatorul_watch = time.time() + 5 if interval_watch else None   # prima verificare imediat dupa pornire
    auto = f"scanare automată la fiecare {SCANARE_AUTOMATA_ORE} h" if interval else "fără scanări automate"
    if interval_watch:
        auto += f", watchlist la fiecare {WATCHLIST_MINUTE} min"
    trimite_meniu(chat_autorizat, f"🤖 <b>Botul a pornit</b> ({auto}).\nCe scanez acum?")
    print(f"Botul ruleaza ({auto}). Ctrl+C ca sa-l opresti.")

    while True:
        try:
            urmatorul_eveniment = min([t for t in (urmatoarea, urmatorul_watch) if t] or [time.time() + 50])
            updates = tg.get_updates(offset, timeout=max(1, min(50, int(urmatorul_eveniment - time.time()))))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[{datetime.now():%H:%M}] Eroare la citirea mesajelor: {e} - reincerc in 15s")
            time.sleep(15)
            continue

        for u in updates:
            offset = u["update_id"] + 1
            if u.get("callback_query"):
                try:
                    trateaza_buton(u["callback_query"], chat_autorizat)
                except Exception:
                    traceback.print_exc()
                continue
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat = str(msg.get("chat", {}).get("id", ""))
            if not text or not chat:
                continue
            if chat != chat_autorizat:
                print(f"Mesaj ignorat din chat neautorizat {chat}: {text[:40]}")
                continue
            try:
                trateaza(text, chat)
            except Exception as e:
                traceback.print_exc()
                tg.send_message(f"⚠️ Eroare: {html.escape(str(e))[:500]}", chat)

        if urmatorul_watch and time.time() >= urmatorul_watch:
            urmatorul_watch = time.time() + interval_watch
            porneste_watchlist(chat_autorizat)

        if urmatoarea and time.time() >= urmatoarea:
            urmatoarea = time.time() + interval
            seteaza_urmatoarea(urmatoarea)
            porneste_scanare(chat_autorizat, automata=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot oprit.")
