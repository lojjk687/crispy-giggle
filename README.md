# ⚽ La Liga AI Dynasty — 10-Season Virtual Football Manager Telegram Bot

Production-ready, 100% zero-touch autonomous Telegram bot built with **Python 3.12**, **Aiogram 3.x**, **SQLite3 (aiosqlite)**, **Pydantic v2**, and **Google Gemini 2.5 Flash (`google-genai`)**.

---

## 🚀 Quick Start (Zero-Touch)

### 1. Setup Environment Variables
Copy `.env.example` to `.env` and fill in your tokens:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCDefghIJKlmnoPQRstuvwxYZ
GEMINI_API_KEY=AIzaSyYourGeminiApiKeyHere
ADMIN_IDS=123456789
GROUP_CHAT_ID=-1001234567890
AUTOPILOT_INTERVAL_HOURS=24
```

### 2. Install Requirements
```bash
pip install -r requirements.txt
```

### 3. Run the Bot
```bash
python main.py
```
> **Notice:** You DO NOT need to run any database creation scripts! `main.py` detects an uninitialized database and automatically creates all tables, seeds 20 clubs, 360 players, generates the full **38-round double round-robin calendar (380 matches)**, and seeds the **Copa del Rey bracket**.

---

## 🎛️ Master Control Panel (`/panel`)
Send `/panel` in Telegram to access all operations via Inline Buttons:
- ⚽ **[Симулировать следующий тур]** — Simulates all matches in the round, updates table, awards TV rights (+2M€), win bonuses (+3M€), and fines (-0.5M€ for red cards).
- 🏋️‍♂️ **[Провести неделю тренировок]** — Conducts weekly XP training for all 20 clubs simultaneously (2x XP for U-21 wonderkids).
- 🔄 **[Трансферы: ОТКРЫТО / ЗАКРЫТО]** — Toggles the transfer window with instant notification.
- 🏆 **[Церемония наград и Новый Сезон]** — Runs the one-click season rollover pipeline.
- 🤖 **[Автопилот: ВКЛ / ВЫКЛ]** — Background worker advances rounds automatically every 24 hours.
- 💾 **[Скачать бэкап базы (.db)]** — Sends the live SQLite database file directly to the administrator in Telegram.

---

## 🏗️ Architectural Upgrades Implemented

1. **Strict Model Lock & Free-Tier Rate Limiting (`ai_service.py`):**
   - Strictly locked to `gemini-2.5-flash`.
   - Async rate limiter enforces a minimum 3.5-second interval between requests (15 RPM protection).
   - Exponential backoff wrapper retries up to 4 times (delays: 5s, 10s, 15s, 20s) on HTTP 429, Quota, or timeout errors.
   - Deterministic realistic fallbacks guarantee the bot **never crashes**.

2. **Autonomous AI Next-Season Budget Engine (`handlers/awards.py`):**
   - Formula: `Remaining Balance - FFP Tax + Placement Prize + TV Rights + Trophies + Dynamic AI Sponsor Evaluation`.
   - Underdogs overperforming gain +25-40% sponsor contracts; failing giants get sponsor cuts.
   - Automatically updates all SQLite club budgets and publishes the full financial report.

3. **One-Click Season Rollover (`/next_season`):**
   - Increments season counter (1 to 10).
   - Ages all players (+1 year), applies OVR decay to veterans (33+), retires aged players, generates 16-18 y.o. academy wonderkids ("regens").
   - Resets standings, clears suspensions/injuries, generates a fresh 380-match schedule, and auto-sends the `.db` backup file to Telegram.

4. **Self-Healing Infinite Loop (`main.py`):**
   - Auto-reconnecting polling lifecycle recovers from internet dropouts, connection timeouts, or Telegram API resets without exiting the Python process.
