import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROUP_CHAT_ID = int(os.getenv("GROUP_CHAT_ID", "0"))
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]
DB_PATH = os.getenv("DB_PATH", "laliga_dynasty.db")
MODEL_NAME = "gemini-2.5-flash"

LEAGUE_PRIZES = {
    1: 50_000_000, 2: 35_000_000, 3: 25_000_000, 4: 18_000_000,
    5: 14_000_000, 6: 11_000_000, 7: 9_000_000, 8: 7_500_000,
    9: 6_000_000, 10: 5_000_000, 11: 4_500_000, 12: 4_000_000,
    13: 3_500_000, 14: 3_000_000, 15: 2_500_000, 16: 2_000_000,
    17: 1_500_000, 18: 1_200_000, 19: 1_000_000, 20: 800_000,
}

TV_RIGHTS_PER_ROUND = 500_000
MATCH_WIN_REVENUE = 3_000_000
MATCH_DRAW_REVENUE = 1_000_000
MATCH_LOSS_REVENUE = 300_000
RED_CARD_FINE = 500_000
AUTOPILOT_INTERVAL_HOURS = 24
