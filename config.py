"""
config.py - Central configuration for La Liga AI Dynasty bot.
Loads environment variables, defines game constants and exports.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Model Lock (Strictly gemini-2.5-flash)
# ---------------------------------------------------------------------------
MODEL_NAME: str = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Environment / secrets with safe fallbacks
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# Comma-separated admin Telegram user IDs → list of ints
ADMIN_IDS: list[int] = [
    int(uid.strip())
    for uid in os.getenv("ADMIN_IDS", "").split(",")
    if uid.strip().isdigit()
]

# Primary Telegram group chat ID
_group_chat_raw = os.getenv("GROUP_CHAT_ID", "0").strip()
GROUP_CHAT_ID: int = int(_group_chat_raw) if _group_chat_raw.lstrip("-").isdigit() else 0

# Autopilot interval (hours between rounds if autopilot is enabled)
AUTOPILOT_INTERVAL_HOURS: int = int(os.getenv("AUTOPILOT_INTERVAL_HOURS", "24"))

# ---------------------------------------------------------------------------
# Game constants
# ---------------------------------------------------------------------------
SEASONS_TOTAL: int = 10
CLUBS_COUNT: int = 20
ROUNDS_TOTAL: int = 38  # Double round-robin (20 teams * 19 opponents)
RETIREMENT_AGE: int = 33  # Age at which OVR decay begins

# Prize money per final league placement (1st to 20th in Euros)
PRIZE_MONEY: dict[int, float] = {
    1:  140_000_000.0,
    2:  120_000_000.0,
    3:  100_000_000.0,
    4:   85_000_000.0,
    5:   72_000_000.0,
    6:   60_000_000.0,
    7:   50_000_000.0,
    8:   42_000_000.0,
    9:   36_000_000.0,
    10:  30_000_000.0,
    11:  26_000_000.0,
    12:  23_000_000.0,
    13:  20_000_000.0,
    14:  18_000_000.0,
    15:  16_000_000.0,
    16:  14_000_000.0,
    17:  12_000_000.0,
    18:  10_000_000.0,
    19:   8_000_000.0,
    20:   6_000_000.0,
}

# Alias for backward and cross-module compatibility
LEAGUE_PRIZES = PRIZE_MONEY

# Red-card fine per incident (applied to the offending club's budget)
RED_CARD_FINE: float = 500_000.0

# Suspension thresholds
YELLOW_CARD_SUSPENSION_THRESHOLD: int = 2  # 2 yellows = 1-match ban
