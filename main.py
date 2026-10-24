"""
main.py — La Liga AI Dynasty Telegram Bot Entrypoint
Features:
1. Auto-seeding on first launch (no manual terminal scripts required).
2. Autonomous 10-season engine & background autopilot worker.
3. Self-healing infinite polling loop with automatic reconnect.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import (
    TELEGRAM_BOT_TOKEN,
    ADMIN_IDS,
    GROUP_CHAT_ID,
    AUTOPILOT_INTERVAL_HOURS,
    SEASONS_TOTAL,
)
from database import (
    get_db,
    init_db,
    auto_seed_if_needed,
    get_current_season,
    get_current_round,
    get_game_state,
    execute_full_season_rollover,
    backup_db,
)
from handlers.admin import router as admin_router
from handlers.claim import router as claim_router
from handlers.matchday import router as matchday_router, simulate_entire_current_round
from handlers.transfers import router as transfers_router
from handlers.training import router as training_router, run_training_for_all_clubs
from handlers.awards import router as awards_router, run_awards_and_budget_pipeline

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("laliga_bot")


# ---------------------------------------------------------------------------
# Autopilot Background Worker (Upgrade #5)
# ---------------------------------------------------------------------------
async def autopilot_worker(bot: Bot) -> None:
    """
    Background worker that runs indefinitely.
    If 'autopilot_enabled' is '1' in game_state, automatically simulates
    rounds and advances the dynasty league on a scheduled timer.
    """
    logger.info("Autopilot worker task started.")
    interval_seconds = max(60, AUTOPILOT_INTERVAL_HOURS * 3600)

    while True:
        try:
            await asyncio.sleep(30)  # Check status every 30 seconds

            async with await get_db() as db:
                enabled = await get_game_state(db, "autopilot_enabled", "0")
                if enabled != "1":
                    continue

                season = await get_current_season(db)
                round_ = await get_current_round(db)

                # Check when the last autopilot round was executed
                last_run_str = await get_game_state(db, "autopilot_last_run", "0")
                now_ts = datetime.utcnow().timestamp()

                if now_ts - float(last_run_str) < interval_seconds:
                    continue

                logger.info("🤖 Autopilot executing Round %d of Season %d...", round_, season)

                # 1. Simulate entire round
                count, summary = await simulate_entire_current_round(bot)
                logger.info("🤖 Autopilot simulated %d matches.", count)

                # 2. Run weekly training for all clubs
                await run_training_for_all_clubs(db)

                # Update last run timestamp
                from database import update_game_state
                await update_game_state(db, "autopilot_last_run", str(now_ts))

                # Check if season complete (round > 38)
                new_round = await get_current_round(db)
                if new_round > 38:
                    logger.info("🏆 Autopilot detected Season %d completion! Running off-season rollover...", season)
                    if season < SEASONS_TOTAL:
                        awards_res, _ = await run_awards_and_budget_pipeline(bot)
                        rollover_summary = await execute_full_season_rollover(db, awards_res.get("new_budgets"))

                        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                        backup_file = f"backup_season{season}_{now_str}.db"
                        backup_db(backup_file)

                        announce = (
                            f"🤖 <b>[АВТОПИЛОТ] СЕЗОН {season} ЗАВЕРШЁН!</b>\n\n"
                            f"🚀 Стартовал Сезон {rollover_summary['new_season']}!\n"
                            f"👴 Завершили карьеру: {rollover_summary['players_retired']}\n"
                            f"🌱 Новых вундеркиндов: {rollover_summary['regens_created']}\n"
                            f"📅 Сформировано 380 матчей нового сезона!"
                        )
                        try:
                            await bot.send_message(GROUP_CHAT_ID, announce, parse_mode="HTML")
                        except Exception:
                            pass

        except asyncio.CancelledError:
            logger.info("Autopilot worker stopped.")
            break
        except Exception as exc:
            logger.exception("Error in autopilot worker: %s", exc)
            await asyncio.sleep(10)


# ---------------------------------------------------------------------------
# Self-Healing Application Polling Lifecycle (Upgrade #2 & #6)
# ---------------------------------------------------------------------------
async def main() -> None:
    logger.info("==================================================")
    logger.info("  Starting La Liga AI Dynasty Telegram Bot (2.5)")
    logger.info("==================================================")

    # 1. Initialize SQLite Database Schema
    logger.info("Verifying database schema...")
    await init_db()

    # 2. Auto-seed on first launch (Upgrade #2)
    async with await get_db() as db:
        seeded = await auto_seed_if_needed(db)
        if seeded:
            logger.info("🎉 Database was empty — AUTO-SEEDED 20 clubs, 360 players, 38-round schedule and Copa bracket!")
        else:
            season = await get_current_season(db)
            round_ = await get_current_round(db)
            logger.info("Database loaded successfully. Current Season: %d | Round: %d", season, round_)

    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env! Please provide the token.")
        sys.exit(1)

    # 3. Initialize Aiogram Bot & Dispatcher
    bot = Bot(
        token=TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register all modular routers
    dp.include_router(admin_router)
    dp.include_router(claim_router)
    dp.include_router(matchday_router)
    dp.include_router(transfers_router)
    dp.include_router(training_router)
    dp.include_router(awards_router)

    # 4. Start Autopilot Background Worker
    autopilot_task = asyncio.create_task(autopilot_worker(bot))

    # 5. Self-Healing Infinite Polling Loop (Upgrade #6)
    logger.info("Bot is ready. Entering self-healing polling loop...")

    reconnect_attempts = 0
    while True:
        try:
            logger.info("Connecting to Telegram Bot API...")
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            reconnect_attempts = 0
        except asyncio.CancelledError:
            logger.info("Bot shutting down gracefully.")
            break
        except Exception as exc:
            reconnect_attempts += 1
            wait_sec = min(30, 5 * reconnect_attempts)
            logger.error(
                "Polling connection lost (%s). Auto-reconnecting in %d seconds (attempt %d)...",
                exc,
                wait_sec,
                reconnect_attempts,
            )
            await asyncio.sleep(wait_sec)

    autopilot_task.cancel()
    await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Process terminated by user.")
