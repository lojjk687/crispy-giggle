"""
handlers/admin.py — La Liga AI Dynasty
Administrative commands, master Telegram control panel (/panel),
and one-click season rollover state machine.
All user-facing text is in Russian.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from typing import Any

import aiosqlite
from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, FSInputFile

from config import ADMIN_IDS, GROUP_CHAT_ID, PRIZE_MONEY, LEAGUE_PRIZES
from database import (
    DB_PATH,
    backup_db,
    execute_full_season_rollover,
    end_season,
    get_club,
    get_club_by_owner,
    get_current_round,
    get_current_season,
    get_db,
    get_game_state,
    get_standings,
    init_db,
    update_club_budget,
    update_game_state,
    create_manager,
)
from keyboards import (
    get_club_menu_keyboard,
    get_standings_nav_keyboard,
    get_admin_panel_keyboard,
    get_season_rollover_keyboard,
)
from handlers.awards import run_awards_and_budget_pipeline
from handlers.training import run_training_for_all_clubs

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIER_EMOJI = {1: "🥇", 2: "🥈", 3: "🥉"}
POSITION_MAP = {
    "GK": "ВРТ",
    "CB": "ЦЗ",
    "LB": "ЛЗ",
    "RB": "ПЗ",
    "CDM": "ОПЗ",
    "CM": "ЦП",
    "CAM": "АТП",
    "LW": "ЛВ",
    "RW": "ПВ",
    "ST": "НАП",
    "CF": "НАП",
}

PLACE_EMOJI = {1: "🥇", 2: "🥈", 3: "🥉"}


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _fmt_budget(amount: float) -> str:
    m = amount / 1_000_000
    if m == int(m):
        return f"{int(m)}M€"
    return f"{m:.1f}M€"


def _place_emoji(pos: int) -> str:
    return PLACE_EMOJI.get(pos, f"{pos}.")


# ---------------------------------------------------------------------------
# /start — welcome
# ---------------------------------------------------------------------------

@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    text = (
        "⚽ <b>Добро пожаловать в La Liga AI Dynasty!</b>\n\n"
        "10-сезонный виртуальный футбольный менеджер Ла Лиги под управлением ИИ Gemini 2.5 Flash.\n\n"
        "<b>📋 Доступные команды:</b>\n\n"
        "👥 <b>Для всех участников:</b>\n"
        "  /claim_club — Выбрать и возглавить свободный клуб\n"
        "  /standings — Турнирная таблица Ла Лиги\n"
        "  /club_info &lt;id&gt; — Информация о клубе\n"
        "  /player_info &lt;id&gt; — Профиль игрока\n"
        "  /list_transfers — Последние трансферы\n\n"
        "🏟️ <b>Для владельцев клубов:</b>\n"
        "  /myclub — Меню моего клуба\n"
        "  /prematch &lt;match_id&gt; — Тактика и капитан перед матчем\n"
        "  /train — Провести недельную тренировку\n"
        "  /buy_external &lt;имя&gt; — Купить внешнюю звезду (+20% наценка)\n"
        "  /transfer_internal — Предложить трансфер другому владельцу\n"
        "  /set_sponsor &lt;safe|risky&gt; — Контракт со спонсором\n\n"
        "🎛️ <b>Для администратора:</b>\n"
        "  /panel — <b>Мастер-панель управления лигой (Zero-Touch)</b>\n"
        "  /simulate_round — Симулировать текущий тур\n"
        "  /next_season — Церемония наград и переход в новый сезон\n"
        "  /backup — Скачать резервную копию базы данных (.db)\n"
    )
    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Master Telegram Control Panel (Upgrade #5)
# ---------------------------------------------------------------------------

async def _build_panel_text_and_kb(db: aiosqlite.Connection) -> tuple[str, Any]:
    season = await get_current_season(db)
    current_round = await get_current_round(db)
    auto_val = await get_game_state(db, "autopilot_enabled", "0")
    autopilot_on = auto_val == "1"
    trans_val = await get_game_state(db, "transfer_window_open", "1")
    transfer_open = trans_val == "1"

    async with db.execute(
        "SELECT COUNT(*) AS c FROM matches WHERE season = ? AND round = ? AND status = 'pending'",
        (season, current_round),
    ) as cur:
        row = await cur.fetchone()
        pending_matches = row["c"] if row else 0

    text = (
        f"🎛️ <b>МАСТЕР-ПАНЕЛЬ УПРАВЛЕНИЯ ЛА ЛИГОЙ</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Сезон:</b> {season} / 10\n"
        f"📅 <b>Текущий тур:</b> {current_round} / 38\n"
        f"⚽ <b>Осталось матчей в туре:</b> {pending_matches}\n"
        f"🔄 <b>Трансферное окно:</b> {'🟢 Открыто' if transfer_open else '🔴 Закрыто'}\n"
        f"🤖 <b>Режим Автопилота:</b> {'🟢 ВКЛЮЧЁН (24ч)' if autopilot_on else '⚪ ВЫКЛЮЧЕН'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Управляйте всеми аспектами сезона с помощью кнопок ниже:</i>"
    )
    kb = get_admin_panel_keyboard(autopilot_on=autopilot_on, transfer_window_open=transfer_open)
    return text, kb


@router.message(Command("panel"))
async def cmd_panel(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Панель управления доступна только создателю лиги.")
        return

    async with await get_db() as db:
        text, kb = await _build_panel_text_and_kb(db)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("panel:"))
async def cb_admin_panel(callback: CallbackQuery) -> None:
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Доступ запрещён.", show_alert=True)
        return

    action = callback.data.split(":")[1]

    # 1. Simulate Round
    if action == "sim_round":
        await callback.answer("⏳ Симуляция тура запущена...", show_alert=False)
        from handlers.matchday import simulate_entire_current_round
        matches_count, summary = await simulate_entire_current_round(callback.bot)
        if matches_count > 0:
            await callback.message.answer(f"✅ Тур успешно симулирован ({matches_count} матчей)!")
        else:
            await callback.message.answer(summary)

    # 2. League-wide Training
    elif action == "train_all":
        await callback.answer("🏋️‍♂️ Проводим тренировки всех 20 клубов...", show_alert=False)
        async with await get_db() as db:
            res = await run_training_for_all_clubs(db)
        await callback.message.answer(
            f"✅ <b>Неделя тренировок завершена!</b>\n"
            f"🏋️ Клубов оттренировано: <b>{res['clubs_trained']}</b>\n"
            f"⭐ Повышений OVR: <b>+{res['upgrades_count']}</b>\n"
            f"💪 Опыта распределено: <b>+{res['total_xp_awarded']} XP</b>\n"
            f"<i>Молодые таланты (U-21) получили удвоенный XP!</i>",
            parse_mode="HTML",
        )

    # 3. Toggle Transfer Window
    elif action == "toggle_transfer":
        async with await get_db() as db:
            curr = await get_game_state(db, "transfer_window_open", "1")
            new_val = "0" if curr == "1" else "1"
            await update_game_state(db, "transfer_window_open", new_val)
        state_str = "ОТКРЫТО 🟢" if new_val == "1" else "ЗАКРЫТО 🔴"
        await callback.answer(f"Трансферное окно: {state_str}", show_alert=True)

    # 4. Next Season confirmation prompt
    elif action == "next_season":
        async with await get_db() as db:
            season = await get_current_season(db)
        await callback.message.edit_text(
            f"⚠️ <b>ВЫ УВЕРЕНЫ, ЧТО ХОТИТЕ ПЕРЕЙТИ В НОВЫЙ СЕЗОН?</b>\n\n"
            f"Будет запущена полная цепочка завершения Сезона {season}:\n"
            f"1. 🏆 Проведение церемонии наград и призовых.\n"
            f"2. 📊 AI-расчёт новых бюджетов на Сезон {season+1}.\n"
            f"3. 👴 Старение ветеранов (33+), регрессия OVR и завершение карьер.\n"
            f"4. 🌱 Выпуск новых вундеркиндов из академии (регены 16-18 лет).\n"
            f"5. 📅 Генерация 38 туров нового сезона и сетки Кубка.\n"
            f"6. 💾 Автоматическая отправка бэкапа базы данных.",
            parse_mode="HTML",
            reply_markup=get_season_rollover_keyboard(),
        )
        return

    # 5. Confirm Rollover Execution (Upgrade #4)
    elif action == "confirm_rollover":
        await callback.answer("🚀 Запуск перехода в новый сезон...", show_alert=False)
        await callback.message.answer("⏳ <i>Шаг 1/3: Проводим церемонию наград и AI-расчёт бюджетов...</i>", parse_mode="HTML")

        try:
            awards_res, budget_report = await run_awards_and_budget_pipeline(callback.bot, callback.message)
            new_budgets = awards_res.get("new_budgets", {})
        except Exception as exc:
            logger.exception("Awards pipeline during rollover failed")
            new_budgets = {}

        await callback.message.answer("⏳ <i>Шаг 2/3: Обновляем возраст игроков, регены и генерируем расписание 38 туров...</i>", parse_mode="HTML")

        async with await get_db() as db:
            summary = await execute_full_season_rollover(db, new_budgets)

        # Step 3: Auto-backup export to Telegram (Upgrade #5)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_season{summary['old_season']}_{now_str}.db"
        backup_db(backup_file)

        try:
            doc = FSInputFile(backup_file, filename=f"LaLiga_Dynasty_Season{summary['old_season']}_End.db")
            await callback.bot.send_document(
                callback.from_user.id,
                doc,
                caption=f"💾 <b>Резервная копия базы данных по итогам Сезона {summary['old_season']}</b>",
                parse_mode="HTML",
            )
        except Exception as exc:
            logger.warning("Could not send backup file to admin: %s", exc)

        rollover_msg = (
            f"🎉 <b>СЕЗОН {summary['old_season']} ОФИЦИАЛЬНО ЗАВЕРШЁН!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🚀 <b>ДОБРО ПОЖАЛОВАТЬ В СЕЗОН {summary['new_season']}!</b>\n\n"
            f"👴 Завершили карьеру: <b>{summary['players_retired']} игроков</b>\n"
            f"🌱 Прибыло регенов-вундеркиндов: <b>{summary['regens_created']} игроков</b>\n"
            f"📅 Сформировано матчей на новый сезон: <b>{summary['matches_created']} матчей (38 туров)</b>\n"
            f"🔓 <b>Предсезонное трансферное окно открыто!</b>\n\n"
            f"Команды могут заключать сделки и готовиться к первому туру!"
        )
        try:
            await callback.bot.send_message(GROUP_CHAT_ID, rollover_msg, parse_mode="HTML")
        except Exception:
            pass

        await callback.message.answer(f"✅ Переход в Сезон {summary['new_season']} успешно завершён!")

    # 6. Cancel Rollover
    elif action == "cancel_rollover":
        await callback.answer("Переход отменён.", show_alert=True)

    # 7. Toggle Autopilot (Upgrade #5)
    elif action == "toggle_autopilot":
        async with await get_db() as db:
            curr = await get_game_state(db, "autopilot_enabled", "0")
            new_val = "0" if curr == "1" else "1"
            await update_game_state(db, "autopilot_enabled", new_val)
        state_str = "ВКЛЮЧЁН 🟢" if new_val == "1" else "ВЫКЛЮЧЕН ⚪"
        await callback.answer(f"Автопилот: {state_str}", show_alert=True)

    # 8. Backup DB file directly to Telegram (Upgrade #5)
    elif action == "backup_db":
        await callback.answer("⏳ Создаём и отправляем файл бэкапа...", show_alert=False)
        now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"backup_{now_str}.db"
        backup_db(backup_file)

        doc = FSInputFile(backup_file, filename=f"LaLiga_Dynasty_{now_str}.db")
        await callback.bot.send_document(
            callback.from_user.id,
            doc,
            caption=f"💾 <b>Резервная копия SQLite базы данных</b>\n🕐 {now_str}",
            parse_mode="HTML",
        )

    # 9. Standings
    elif action == "standings":
        async with await get_db() as db:
            season = await get_current_season(db)
        kb = get_standings_nav_keyboard(season)
        await callback.message.answer("📊 Выберите раздел таблицы:", reply_markup=kb)

    # Refresh panel message
    async with await get_db() as db:
        text, kb = await _build_panel_text_and_kb(db)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Direct /next_season command
# ---------------------------------------------------------------------------

@router.message(Command("next_season"))
async def cmd_next_season(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Доступно только администраторам.")
        return

    async with await get_db() as db:
        season = await get_current_season(db)

    await message.answer(
        f"⚠️ <b>Переход в Сезон {season + 1}?</b>\n\n"
        f"Нажмите кнопку ниже для подтверждения запуска церемонии и генерации нового сезона:",
        parse_mode="HTML",
        reply_markup=get_season_rollover_keyboard(),
    )


# ---------------------------------------------------------------------------
# Direct /backup command with FSInputFile sending
# ---------------------------------------------------------------------------

@router.message(Command("backup"))
async def cmd_backup(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администраторам.")
        return

    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{now_str}.db"
    backup_db(filename)

    try:
        doc = FSInputFile(filename, filename=f"LaLiga_backup_{now_str}.db")
        await message.answer_document(
            doc,
            caption=f"✅ <b>Резервная копия базы данных</b>\n🕐 {now_str}",
            parse_mode="HTML",
        )
    except Exception as exc:
        await message.answer(f"✅ Бэкап сохранён локально: <code>{filename}</code>\n(Ошибка отправки файла: {exc})", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /register_club <club_id> <user_id>
# ---------------------------------------------------------------------------

@router.message(Command("register_club"))
async def cmd_register_club(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Команда доступна только администраторам.")
        return

    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("❌ Использование: /register_club &lt;club_id&gt; &lt;user_id&gt;", parse_mode="HTML")
        return

    try:
        club_id = int(parts[1])
        user_id = int(parts[2])
    except ValueError:
        await message.answer("❌ club_id и user_id должны быть целыми числами.")
        return

    async with await get_db() as db:
        club = await get_club(db, club_id)
        if club is None:
            await message.answer(f"❌ Клуб #{club_id} не найден.")
            return

        await create_manager(db, user_id=user_id, club_id=club_id)
        await db.commit()

    await message.answer(
        f"✅ <b>Клуб зарегистрирован!</b>\n\n"
        f"🏟️ Клуб: <b>{club['name']}</b> (ID: {club_id})\n"
        f"👤 Владелец: <code>{user_id}</code>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /standings
# ---------------------------------------------------------------------------

@router.message(Command("standings"))
async def cmd_standings(message: Message) -> None:
    async with await get_db() as db:
        season = await get_current_season(db)
        round_ = await get_current_round(db)
        rows = await get_standings(db, season)

    if not rows:
        await message.answer("📊 Турнирная таблица пока пуста.")
        return

    lines = [
        f"📊 <b>ТАБЛИЦА ЛА ЛИГИ | Сезон {season} | Тур {round_} / 38</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for pos, r in enumerate(rows, start=1):
        lines.append(
            f"{_place_emoji(pos)} {r['club_name']:<16} | {r['played']}игр | {r['wins']}В {r['draws']}Н {r['losses']}П | {r['gf']}:{r['ga']} | <b>{r['points']}pts</b>"
        )

    text = "\n".join(lines)
    kb = get_standings_nav_keyboard(season)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


# ---------------------------------------------------------------------------
# /myclub
# ---------------------------------------------------------------------------

@router.message(Command("myclub"))
async def cmd_myclub(message: Message) -> None:
    user_id = message.from_user.id
    async with await get_db() as db:
        club = await get_club_by_owner(db, user_id)
        if not club:
            await message.answer("❌ У вас нет закреплённого клуба. Попросите администратора зарегистрировать вас.")
            return

        club_id = club["id"]
        season = await get_current_season(db)
        round_ = await get_current_round(db)
        players = await database_get_players_by_ovr(db, club_id)
        standings = await get_standings(db, season)
        pos = next((i for i, s in enumerate(standings, 1) if s["club_id"] == club_id), "—")

    text = (
        f"{TIER_EMOJI.get(club.get('tier', 2), '🥈')} <b>{club['name']}</b>\n"
        f"💰 Бюджет: <b>{_fmt_budget(club['budget'])}</b>\n"
        f"📊 Место в лиге: <b>{pos}</b> | Сезон {season} | Тур {round_}\n\n"
        f"👥 <b>Топ игроки по OVR:</b>\n"
    )
    for p in players[:5]:
        inj = " 🏥Травма" if p.get("injury_rounds", 0) > 0 else ""
        sus = " 🟥Дискв" if p.get("suspension_rounds", 0) > 0 else ""
        text += f"  • {p['name']} ({p['position']}, {p['age']}л) — OVR <b>{p['ovr']}</b>{inj}{sus}\n"

    kb = get_club_menu_keyboard(club_id, is_owner=True)
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


async def database_get_players_by_ovr(db: aiosqlite.Connection, club_id: int):
    async with db.execute("SELECT * FROM players WHERE club_id = ? AND is_retired = 0 ORDER BY ovr DESC", (club_id,)) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------------------
# /set_sponsor <safe|risky>
# ---------------------------------------------------------------------------

@router.message(Command("set_sponsor"))
async def cmd_set_sponsor(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2 or parts[1].lower() not in ("safe", "risky"):
        await message.answer("❌ Использование: /set_sponsor &lt;safe|risky&gt;", parse_mode="HTML")
        return

    mode = parts[1].lower()
    user_id = message.from_user.id
    async with await get_db() as db:
        club = await get_club_by_owner(db, user_id)
        if not club:
            await message.answer("❌ У вас нет клуба.")
            return
        await db.execute("UPDATE clubs SET sponsor_contract = ? WHERE id = ?", (mode, club["id"]))
        await db.commit()

    label = "Надёжный (гарантированные бонусы за победы/ничьи)" if mode == "safe" else "Рисковый (крупный куш за победу, штраф за поражение)"
    await message.answer(f"✅ Спонсорский контракт клуба «{club['name']}» изменён на: <b>{label}</b>.", parse_mode="HTML")


# ---------------------------------------------------------------------------
# /club_info & /player_info
# ---------------------------------------------------------------------------

@router.message(Command("club_info"))
async def cmd_club_info(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("❌ Использование: /club_info &lt;id&gt;", parse_mode="HTML")
        return
    cid = int(parts[1])
    async with await get_db() as db:
        club = await get_club(db, cid)
        if not club:
            await message.answer(f"❌ Клуб #{cid} не найден.")
            return
        players = await get_players_count(db, cid)

    text = (
        f"🏟️ <b>{club['name']}</b> (ID: {cid})\n"
        f"💰 Бюджет: <b>{_fmt_budget(club['budget'])}</b>\n"
        f"👥 Игроков в составе: <b>{players}</b>\n"
        f"💼 Спонсор: <b>{club['sponsor_contract']}</b>"
    )
    await message.answer(text, parse_mode="HTML")


async def get_players_count(db: aiosqlite.Connection, cid: int) -> int:
    async with db.execute("SELECT COUNT(*) as c FROM players WHERE club_id = ? AND is_retired = 0", (cid,)) as cur:
        row = await cur.fetchone()
        return row["c"] if row else 0


@router.message(Command("player_info"))
async def cmd_player_info(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("❌ Использование: /player_info &lt;id&gt;", parse_mode="HTML")
        return
    pid = int(parts[1])
    async with await get_db() as db:
        async with db.execute("SELECT p.*, c.name as club_name FROM players p JOIN clubs c ON p.club_id = c.id WHERE p.id = ?", (pid,)) as cur:
            row = await cur.fetchone()
            if not row:
                await message.answer("❌ Игрок не найден.")
                return
            p = dict(row)

    text = (
        f"👤 <b>{p['name']}</b> (ID: {pid})\n"
        f"🏟️ Клуб: <b>{p['club_name']}</b>\n"
        f"📌 Позиция: <b>{p['position']}</b> | Возраст: <b>{p['age']}</b> лет\n"
        f"⭐ Рейтинг OVR: <b>{p['ovr']}</b> (XP: {p['xp']})\n"
        f"⚽ Голы: {p['goals']} | 🎯 Ассисты: {p['assists']}\n"
        f"🟨 Жёлтые: {p['yellow_cards']} | 🟥 Красные: {p['red_cards']}"
    )
    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# Standings Callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("standings:"))
async def cb_standings(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        return
    v_type, s_str = parts[1], parts[2]
    season = int(s_str)

    async with await get_db() as db:
        round_ = await get_current_round(db)
        if v_type == "liga":
            rows = await get_standings(db, season)
            lines = [f"📊 <b>ТАБЛИЦА ЛА ЛИГИ | Сезон {season} | Тур {round_}</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for pos, r in enumerate(rows, 1):
                lines.append(f"{_place_emoji(pos)} {r['club_name']:<16} | {r['played']}игр | {r['gf']}:{r['ga']} | <b>{r['points']}pts</b>")
            text = "\n".join(lines)
        elif v_type == "scorers":
            async with db.execute("SELECT p.name, p.goals, p.assists, c.name as club_name FROM players p JOIN clubs c ON p.club_id = c.id WHERE p.goals > 0 ORDER BY p.goals DESC, p.assists DESC LIMIT 10") as cur:
                scorers = [dict(r) for r in await cur.fetchall()]
            lines = [f"🌟 <b>ТОП БОМБАРДИРЫ | Сезон {season}</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for pos, s in enumerate(scorers, 1):
                lines.append(f"{_place_emoji(pos)} {s['name']} ({s['club_name']}) — ⚽{s['goals']} 🎯{s['assists']}")
            text = "\n".join(lines) if scorers else "Голов пока не забито."
        else:
            async with db.execute("SELECT cb.*, c1.name as c1_name, c2.name as c2_name FROM copa_bracket cb JOIN clubs c1 ON cb.club1_id = c1.id JOIN clubs c2 ON cb.club2_id = c2.id WHERE cb.season = ?", (season,)) as cur:
                bracket = [dict(r) for r in await cur.fetchall()]
            lines = [f"🏆 <b>КУБОК ДЕЛЬ РЕЙ | Сезон {season}</b>", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for m in bracket:
                lines.append(f"  ⚔️ {m['c1_name']} vs {m['c2_name']}")
            text = "\n".join(lines) if bracket else "Сетка ещё формируется."

    kb = get_standings_nav_keyboard(season)
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
    await callback.answer()
