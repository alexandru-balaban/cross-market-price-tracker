# Cross-Market Price Tracker

Automated tool that scans listings on **999.md** (Moldova) and **Kleinanzeigen** (Munich, Germany) for the same product models, converts prices to EUR, and flags listings priced well below the other market's median — with Telegram alerts.

## What it does

- Scans 5 categories: cameras, over-ear headphones, consoles, controllers, and 144Hz+ gaming monitors — model lists are plain text files (`modele*.txt`), easy to extend without touching code
- Matches listing titles to specific models with regex, filtering out accessories, wrong sibling models (e.g. PS5 Pro vs. Slim), and multi-product "shop" listings
- Converts all prices to EUR using the live BNM exchange rate for MDL
- Tracks listings disappearing across scans to estimate which price gaps reflect real sales vs. scraping noise, instead of trusting a single snapshot
- Sends Telegram alerts for listings priced significantly below the other market's median
- Stores full price history in SQLite for trend analysis

## Stack

Python 3, `requests`, SQLite — no frameworks, built deliberately simple as a learning project.

- `compara.py` — core scraping + comparison engine
- `bot.py` — Telegram bot (commands, scheduled scans)
- `scraper_999.py` / `scraper_kleinanzeigen.py` — site-specific scraping (999.md via its GraphQL API, Kleinanzeigen via HTML)
- `verifica_vanzari.py` — sale-verification pass over historical scans
- `modele*.txt` — editable per-category model lists

## Setup

1. `pip install requests` (plus your Telegram bot library — see imports in `bot.py`)
2. Copy `.env.example` to `.env` and fill in your Telegram bot token/chat ID
3. `python3 compara.py` — run all categories once, or `python3 compara.py <categorie>` for one
4. `python3 bot.py` — run the scheduled bot

## Roadmap

- Currently runs from a laptop, on demand
- **Next step: move it to a Raspberry Pi** for unattended 24/7 scanning
- Possible later additions: outlier-price detection, more categories

---
Personal project built to get hands-on with Python, web scraping, and small-scale automation.
