# Live alerts — free, automated setup (GitHub Actions + Telegram)

This runs the tested EMA/VWAP strategy **automatically every 15 minutes during market hours**,
every weekday, and sends the trade setup to your **Telegram**. It is **free** (no credit card)
and runs **without your PC being on**.

It sends **alerts only** — it never places orders.

---

## How it works (30-second version)

- **GitHub Actions** triggers the scanner on a schedule, once every 15 minutes from 09:15 to 15:30 IST, Mon–Fri.
- The scanner checks the **last closed 15-minute candle**, and if a `trend` setup fired it sends a Telegram alert with strike, entry, SL, target and quantity.
- The scanner also **skips the lunch / danger zone** (10:00–11:00 by default) and ignores stale candles, so you don't get junk alerts.
- Your secret keys (FYERS + Telegram) are stored safely as **GitHub Secrets**.

You set it up once. After that it just runs.

---

## Before you start — checklist

You need:

1. A **GitHub account** (free) — https://github.com
2. Your **FYERS** `client id` and **access token**
3. A **Telegram bot** token and your **chat id** (Part 2 below)

---

## Part 1 — Put the code on GitHub (5 minutes)

1. Log in to GitHub → click **+** (top-right) → **New repository**.
2. Name it e.g. `sensex-scanner`. Choose **Private** (recommended — your code and state stay hidden).
   **Do NOT** tick "Add a README".
3. Click **Create repository**.
4. On the new repo page, click the **"uploading an existing file"** link.
5. Drag **all the project files** into the upload box:
   - `live_scanner.py`
   - `app.py`, `config.json`, `requirements.txt`, `requirements-scanner.txt`
   - the whole `src` folder
   - the `.github` folder (**important** — do this one, or the automation won't exist)
   - `tests` folder (optional)
   - **Do NOT upload** `.env` or `.venv` (never upload secrets or the virtualenv).
6. Click **Commit changes**.

> If the `.github/workflows/scanner.yml` file doesn't appear, GitHub may have hidden the folder.
> Easiest fix: use the GitHub web "Add file → Create new file", type
> `.github/workflows/scanner.yml` as the name, and paste the file contents.

---

## Part 2 — Create your Telegram bot (5 minutes)

1. In Telegram, search **@BotFather** → click **Start** → send `/newbot`.
2. Give it a name (e.g. `My Sensex Alerts`) and a username ending in `bot`.
3. BotFather replies with a **token** like `123456789:ABCdefGhIJK...`. **Copy it.**
4. Search **@userinfobot** → click **Start**. It replies with your numeric **Id** (e.g. `987654321`). **Copy it.**
5. Open your new bot (the one from step 2) and press **Start** — this lets it message you.

---

## Part 3 — Add your secrets to GitHub (3 minutes)

In your GitHub repo: **Settings** (top menu) → **Secrets and variables** → **Actions** →
**New repository secret**. Add these **four** secrets, one at a time:

| Name (exactly) | Value |
|---|---|
| `FYERS_CLIENT_ID` | your FYERS app id, e.g. `ABC12345-100` |
| `FYERS_ACCESS_TOKEN` | your current FYERS access token |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the numeric id from @userinfobot |

Names must match **exactly** (all caps, underscores).

---

## Part 4 — Turn it on

1. Go to the **Actions** tab in your repo.
2. If you see a prompt, click **"I understand my workflows, go ahead and enable them."**
3. Click **Market scanner** in the left list → click **Run workflow** → **Run workflow**.
4. Click the running job → open the **Scan once** step to see the log.

What you should see:

- `market closed; waiting...` or a scan line with a reason such as `no_setup`, `already_alerted`,
  `blackout_window`, or `alert_sent`.
- If a setup exists and you enabled Telegram, a message arrives in your chat.

That's it — from now on it runs **automatically every 15 minutes on weekdays**.

---

## Testing without waiting for a real signal

To confirm Telegram works end-to-end:

1. In the repo, open the **Live scanner** tab of the dashboard (or run locally), and click
   **Send test Telegram message** — you should receive it instantly.
2. Or run locally once: `python live_scanner.py --dry-run --once` prints a sample alert to the
   console without sending.

---

## The lunch / danger-zone skip (already on)

In `config.json`:

```json
"no_entry_windows": ["10:00-11:00"]
```

During those windows the scanner will **not** alert. `10:00–11:00` was the only losing hour in
your own backtest (+₹10k at 09:00, **−₹6.4k at 10:00**, positive for every other hour), so it is
off by default. To also skip classic lunch, change it to `["10:00-11:00", "12:00-13:30"]`.
Use `"HH:MM-HH:MM"` strings in IST.

---

## The important, honest parts

- **FYERS tokens expire daily.** This is the one chore. Each morning you must update the
  `FYERS_ACCESS_TOKEN` secret, OR add the optional auto-login script (ask me and I'll build it).
  Without a valid token the runs will log errors and send nothing.
- **Schedule timing:** GitHub's scheduler can run a few minutes late under load. Fine for a
  15-minute system, but not second-exact.
- **Dedup:** each run remembers what it alerted (via a cache), so you won't get the same candle twice.
- **Auto-disable:** GitHub pauses scheduled workflows after **60 days with no repo commits**. To keep
  it alive, open the repo and make a tiny commit once a month (e.g. edit this README, add a space).
  Or re-enable it from the Actions tab.
- **Private repo minutes:** ~1,100/month used out of 2,000 free. If you make the repo **public**,
  minutes are unlimited — but anyone can see the code (never your secrets).
- **Alerts, not auto-trading.** You place the order yourself. Use small size; this is a research
  tool, not guaranteed income.

---

## Quick reference — file map

| File | What it does |
|---|---|
| `.github/workflows/scanner.yml` | The scheduler: runs the scanner every 15 min, weekdays |
| `live_scanner.py` | The scanner: signals, blackout, dedup, Telegram |
| `src/telegram_notifier.py` | Sends the Telegram message |
| `src/fyers_client.py` | FYERS data + live quotes |
| `config.json` | Strategy settings incl. `no_entry_windows` |
