"""main.py — La Liga AI Dynasty Bot entry point."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import BotCommand, BotCommandScopeDefault, Message

from config import ADMIN_IDS, AUTOPILOT_INTERVAL_HOURS, BOT_TOKEN, GROUP_CHAT_ID
from database import auto_seed_if_needed, get_db, get_game_state, init_db

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bot & Dispatcher
# ---------------------------------------------------------------------------
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()

# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------
from handlers import admin, awards, claim, matchday, national_team, sponsors, training, transfers

dp.include_router(claim.router)
dp.include_router(admin.router)
dp.include_router(transfers.router)
dp.include_router(sponsors.router)
dp.include_router(national_team.router)
dp.include_router(training.router)
dp.include_router(matchday.router)
dp.include_router(awards.router)


# ---------------------------------------------------------------------------
# /start and /help
# ---------------------------------------------------------------------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "⚽ <b>Добро пожаловать в La Liga AI Dynasty!</b>\n\n"
        "Это 10-сезонный симулятор футбольного менеджера в стиле Ла Лиги.\n\n"
        "🎮 <b>Быстрый старт:</b>\n"
        "1. /claim_club — выберите свой клуб\n"
        "2. /myclub — посмотрите состав\n"
        "3. /train Атака — проведите тренировку\n"
        "4. /prematch — настройте команду перед матчем\n\n"
        "Введите /help для полного списка команд. ¡Hala Madrid! 🔥"
    )


@dp.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>Команды La Liga AI Dynasty</b>\n\n"
        "🏟 <b>Клуб и игроки:</b>\n"
        "/claim_club — выбрать клуб\n"
        "/myclub — информация о вашем клубе\n"
        "/club_info &lt;название&gt; — информация о клубе\n"
        "/player_info &lt;имя&gt; — статистика игрока\n"
        "/standings — турнирная таблица\n\n"
        "🏋️ <b>Тренировки:</b>\n"
        "/train &lt;Атака|Оборона|Физика|Академия&gt; — тренировка\n"
        "/training_history — история тренировок\n\n"
        "💰 <b>Трансферы:</b>\n"
        "/buy_external &lt;имя&gt; — купить игрока с рынка\n"
        "/transfer_internal @продавец Имя Сумма — сделка с другим менеджером\n"
        "/list_transfers — список трансферов сезона\n\n"
        "⚽ <b>Матчи:</b>\n"
        "/prematch — предматчевое меню\n"
        "/prematch &lt;match_id&gt; — меню конкретного матча\n\n"
        "📊 <b>Статистика:</b>\n"
        "/season_stats — лучшие игроки сезона\n\n"
        "🔑 <b>Только для администраторов:</b>\n"
        "/panel — панель управления\n"
        "/simulate_round — симулировать тур\n"
        "/simulate_match &lt;id&gt; — симулировать матч\n"
        "/next_round — следующий тур\n"
        "/next_season — следующий сезон\n"
        "/awards — церемония наград\n"
        "/transfer_window open|close — управление окном\n"
        "/set_sponsor &lt;low|medium|high&gt; — спонсорский контракт\n"
        "/sponsors — спонсорские предложения\n"
        "/free_agents — рынок свободных агентов\n"
        "/sign_free_agent &lt;имя&gt; — подписать свободного агента\n"
        "/friendly @соперник — товарищеский матч\n"
        "/set_tactic &lt;схема&gt; — выбрать тактическую схему\n"
        "/claim_national_team — возглавить сборную\n"
        "/my_national_team — моя сборная\n"
        "/national_squad — заявка сборной\n"
        "/callup &lt;имя&gt; — вызвать игрока в сборную\n"
        "/release_player &lt;имя&gt; — исключить игрока из сборной\n"
        "/backup — бэкап базы данных\n"
        "/reset_season — сбросить сезон в начало\n"
        "/offer_sponsors — разослать спонсоров всем (админ)\n"
        "/international_break — перерыв на сборные (админ)\n"
    )


async def setup_bot_commands(bot_instance: Bot):
    """Register bot commands in Telegram Menu button."""
    commands = [
        BotCommand(command="start", description="🚀 Запуск и быстрый старт"),
        BotCommand(command="help", description="📖 Справка по всем командам"),
        BotCommand(command="claim_club", description="⚽ Выбрать футбольный клуб"),
        BotCommand(command="myclub", description="🏟 Мой клуб, бюджет и состав"),
        BotCommand(command="standings", description="📊 Турнирная таблица"),
        BotCommand(command="prematch", description="⚔️ Предматчевая настройка (тактика, капитан)"),
        BotCommand(command="set_tactic", description="📐 Выбрать тактическую схему клуба"),
        BotCommand(command="sponsors", description="🏢 Контракты и предложения спонсоров"),
        BotCommand(command="free_agents", description="🆓 Список свободных агентов"),
        BotCommand(command="sign_free_agent", description="✍️ Подписать свободного агента"),
        BotCommand(command="friendly", description="🤝 Вызов на товарищеский матч"),
        BotCommand(command="claim_national_team", description="🌍 Возглавить национальную сборную"),
        BotCommand(command="my_national_team", description="🏳️ Моя сборная и статус"),
        BotCommand(command="national_squad", description="📋 Заявка сборной на турнир"),
        BotCommand(command="callup", description="📢 Вызвать игрока в сборную"),
        BotCommand(command="release_player", description="👋 Исключить игрока из сборной"),
        BotCommand(command="train", description="🏋️ Провести тренировку (Атака, Оборона...)"),
        BotCommand(command="training_history", description="📋 История тренировок клуба"),
        BotCommand(command="buy_external", description="💰 Купить игрока со свободного рынка"),
        BotCommand(command="transfer_internal", description="🤝 Внутренний трансфер между клубами"),
        BotCommand(command="list_transfers", description="📜 Список трансферов сезона"),
        BotCommand(command="season_stats", description="🌟 Топ бомбардиров и карточек"),
        BotCommand(command="club_info", description="ℹ️ Информация о любом клубе"),
        BotCommand(command="player_info", description="👤 Карточка игрока и характеристики"),
        BotCommand(command="panel", description="⚙️ Панель администратора"),
        BotCommand(command="simulate_round", description="▶️ Симулировать текущий тур (админ)"),
        BotCommand(command="next_season", description="🏆 Перейти в следующий сезон (админ)"),
        BotCommand(command="reset_season", description="🔄 Полный сброс сезона в начало (админ)"),
        BotCommand(command="backup", description="💾 Скачать бэкап базы данных (админ)"),
    ]
    try:
        await bot_instance.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("Bot commands successfully registered in Telegram menu.")
    except Exception as e:
        logger.error(f"Failed to set bot commands: {e}")


# ---------------------------------------------------------------------------
# Autopilot background worker
# ---------------------------------------------------------------------------

async def autopilot_worker():
    """Background task that auto-simulates rounds if autopilot is on."""
    interval = AUTOPILOT_INTERVAL_HOURS * 3600
    logger.info(f"Autopilot worker started (interval: {AUTOPILOT_INTERVAL_HOURS}h)")

    while True:
        await asyncio.sleep(interval)
        try:
            async with get_db() as db:
                pilot_state = await get_game_state(db, "autopilot", "off")

            if pilot_state != "on":
                continue

            logger.info("Autopilot: simulating round…")
            # Build a fake message-like context for simulate_round
            from aiogram.types import Chat, User
            # We'll directly call the logic
            async with get_db() as db:
                from database import get_current_round, get_current_season, update_game_state
                season = await get_current_season(db)
                rnd = await get_current_round(db)

                async with db.execute(
                    "SELECT id FROM matches WHERE season=? AND round=? AND status='scheduled'",
                    (season, rnd),
                ) as cur:
                    rows = await cur.fetchall()

            match_ids = [r["id"] for r in rows]
            if not match_ids:
                logger.info("Autopilot: no matches to simulate")
                continue

            from handlers.matchday import _simulate_one_match

            # We need a Message object — use bot to send to group and collect report
            class FakeMessage:
                async def answer(self, text, **kwargs):
                    try:
                        await bot.send_message(GROUP_CHAT_ID, text, **kwargs)
                    except Exception as e:
                        logger.warning(f"Autopilot group post failed: {e}")

                @property
                def bot(self):
                    return bot

            fake_msg = FakeMessage()
            for mid in match_ids:
                await _simulate_one_match(fake_msg, mid, fast_mode=True)

            async with get_db() as db:
                await update_game_state(db, "round", str(rnd + 1))

            logger.info(f"Autopilot: round {rnd} simulated, now round {rnd + 1}")

        except Exception as e:
            logger.error(f"Autopilot error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main():
    # Init DB
    await init_db()
    async with get_db() as db:
        await auto_seed_if_needed(db)

    logger.info("Database ready. Starting bot…")

    # Set bot commands in Telegram menu (shows on typing '/')
    await setup_bot_commands(bot)

    # Start autopilot as background task
    asyncio.create_task(autopilot_worker())

    # Self-healing polling loop
    while True:
        try:
            logger.info("Starting bot polling…")
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
        except Exception as e:
            logger.error(f"Polling crashed: {e}", exc_info=True)
            logger.info("Reconnecting in 5 seconds…")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
