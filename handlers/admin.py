"""handlers/admin.py — Admin panel and informational commands."""

import logging
import os

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import ADMIN_IDS, DB_PATH, GROUP_CHAT_ID
from database import (
    get_all_clubs,
    get_club,
    get_club_by_owner,
    get_current_round,
    get_current_season,
    get_db,
    get_manager,
    get_player_by_name,
    get_players,
    get_standings,
    reset_current_season,
    update_game_state,
)
from keyboards import get_admin_panel_keyboard, get_reset_season_confirm_keyboard

router = Router()
logger = logging.getLogger(__name__)

POSITION_EMOJI = {
    "GK": "🧤", "CB": "🛡", "LB": "⬅️", "RB": "➡️",
    "CDM": "🔒", "CM": "⚙️", "CAM": "🎯", "LW": "⚡",
    "RW": "⚡", "ST": "⚽", "Utility": "🔄",
}


def _fmt_budget(amount: int) -> str:
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M€"
    return f"{amount:,}€"


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ---------------------------------------------------------------------------
# /panel
# ---------------------------------------------------------------------------
@router.message(Command("panel"))
async def cmd_panel(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет доступа к панели администратора.")
        return

    async with get_db() as db:
        season = await get_current_season(db)
        rnd = await get_current_round(db)

    await message.answer(
        f"⚙️ <b>Панель администратора La Liga Dynasty</b>\n\n"
        f"📅 Текущий сезон: <b>{season}</b>\n"
        f"🔄 Текущий тур: <b>{rnd}</b>",
        parse_mode="HTML",
        reply_markup=get_admin_panel_keyboard(),
    )


# ---------------------------------------------------------------------------
# Admin panel callbacks
# ---------------------------------------------------------------------------
@router.callback_query(F.data.startswith("admin:"))
async def cb_admin_panel(callback: CallbackQuery):
    if not _is_admin(callback.from_user.id):
        await callback.answer("⛔ Только для администраторов!", show_alert=True)
        return

    action = callback.data.split(":")[1]

    if action == "standings":
        await callback.answer()
        async with get_db() as db:
            season = await get_current_season(db)
            rows = await get_standings(db, season)
        text = _format_standings(rows, season)
        await callback.message.answer(text, parse_mode="HTML")

    elif action == "backup":
        await callback.answer("📦 Создаю бэкап…")
        if os.path.exists(DB_PATH):
            doc = FSInputFile(DB_PATH, filename=f"laliga_backup_s{os.path.getsize(DB_PATH)}.db")
            await callback.message.answer_document(doc, caption="💾 Резервная копия базы данных")
        else:
            await callback.message.answer("❌ Файл базы данных не найден.")

    elif action == "autopilot_on":
        async with get_db() as db:
            await update_game_state(db, "autopilot", "on")
        await callback.answer("🤖 Автопилот включён!", show_alert=True)

    elif action == "autopilot_off":
        async with get_db() as db:
            await update_game_state(db, "autopilot", "off")
        await callback.answer("🤖 Автопилот выключен!", show_alert=True)

    elif action == "tw_open":
        async with get_db() as db:
            await update_game_state(db, "transfer_window", "open")
        await callback.answer("🪟 Трансферное окно открыто!", show_alert=True)
        await callback.bot.send_message(
            GROUP_CHAT_ID,
            "🚨 <b>Трансферное окно открыто!</b>\n\nТеперь можно совершать трансферы. Используйте /transfer_internal и /buy_external.",
            parse_mode="HTML",
        )

    elif action == "tw_close":
        async with get_db() as db:
            await update_game_state(db, "transfer_window", "closed")
        await callback.answer("🚫 Трансферное окно закрыто!", show_alert=True)
        await callback.bot.send_message(
            GROUP_CHAT_ID,
            "🔒 <b>Трансферное окно закрыто!</b>\n\nТрансферы временно недоступны.",
            parse_mode="HTML",
        )

    elif action == "next_season":
        await callback.answer("⏳ Запускаю смену сезона…")
        await callback.message.answer("🏆 Начинаю процедуру смены сезона…")
        # Import here to avoid circular
        from handlers.awards import _do_next_season
        await _do_next_season(callback.message, callback.bot)

    elif action == "awards":
        await callback.answer()
        from handlers.awards import cmd_awards
        await cmd_awards(callback.message)

    elif action == "reset_season_confirm":
        await callback.answer()
        await callback.message.answer(
            "⚠️ <b>Внимание! Сброс сезона</b>\n\n"
            "Это действие сбросит лигу в <b>Сезон 1, Тур 1</b>, восстановит стартовые бюджеты клубов, "
            "очистит статистику матчей, турнирную таблицу и сгенерирует новый календарь игр.\n\n"
            "Вы уверены?",
            parse_mode="HTML",
            reply_markup=get_reset_season_confirm_keyboard(),
        )

    elif action == "cancel_reset":
        await callback.answer("Сброс отменён.")
        await callback.message.edit_text("❌ Сброс сезона отменён.")

    elif action == "do_reset_season":
        await callback.answer("⏳ Выполняю сброс сезона…")
        async with get_db() as db:
            await reset_current_season(db, reset_to_season_1=True)
        await callback.message.edit_text(
            "✅ <b>Сезон успешно сброшен!</b>\n\n"
            "• Установлен <b>Сезон 1, Тур 1</b>\n"
            "• Сгенерирован новый календарь из 38 туров\n"
            "• Таблица и статистика обнулены\n"
            "• Бюджеты клубов возвращены к стартовым\n"
            "• Трансферное окно открыто",
            parse_mode="HTML",
        )
        await callback.bot.send_message(
            GROUP_CHAT_ID,
            "🔄 <b>Администратор выполнил полный сброс лиги!</b>\n\n"
            "Добро пожаловать в <b>Сезон 1</b>! Календарь сформирован, можно готовиться к матчам и совершать трансферы! ⚽",
            parse_mode="HTML",
        )

    elif action == "simulate_round":
        await callback.answer("⏳ Запускаю симуляцию тура…")
        from handlers.matchday import cmd_simulate_round
        await cmd_simulate_round(callback.message)

    else:
        await callback.answer(f"❓ Неизвестное действие: {action}", show_alert=True)


# ---------------------------------------------------------------------------
# /standings
# ---------------------------------------------------------------------------
def _format_standings(rows: list, season: int) -> str:
    if not rows:
        return f"📊 Таблица сезона {season} пуста."

    header = f"📊 <b>Турнирная таблица La Liga — Сезон {season}</b>\n\n"
    header += "<pre>"
    header += f"{'#':>2} {'Клуб':<20} {'И':>3} {'В':>3} {'Н':>3} {'П':>3} {'О':>4}\n"
    header += "─" * 40 + "\n"

    lines = []
    for i, row in enumerate(rows, start=1):
        name = row.get("short_name") or row.get("name", "?")
        name = name[:8]
        played = row.get("played", 0)
        wins = row.get("wins", 0)
        draws = row.get("draws", 0)
        losses = row.get("losses", 0)
        pts = row.get("points", 0)
        medal = "🥇" if i == 1 else ("🥈" if i == 2 else ("🥉" if i == 3 else f"{i:>2}"))
        lines.append(f"{medal} {name:<8} {played:>3} {wins:>3} {draws:>3} {losses:>3} {pts:>4}")

    return header + "\n".join(lines) + "</pre>"


@router.message(Command("standings"))
async def cmd_standings(message: Message):
    async with get_db() as db:
        season = await get_current_season(db)
        rows = await get_standings(db, season)
    text = _format_standings(rows, season)
    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /myclub
# ---------------------------------------------------------------------------
@router.message(Command("myclub"))
async def cmd_myclub(message: Message):
    async with get_db() as db:
        manager = await get_manager(db, message.from_user.id)
        if not manager:
            await message.answer(
                "⚽ Вы ещё не выбрали клуб!\nИспользуйте /claim_club чтобы начать."
            )
            return

        club = await get_club(db, manager["club_id"])
        if not club:
            await message.answer("❌ Ошибка: клуб не найден.")
            return

        players = await get_players(db, club["id"])
        season = await get_current_season(db)
        standings = await get_standings(db, season)

    # Find club position in table
    position = next(
        (i + 1 for i, r in enumerate(standings) if r["club_id"] == club["id"]),
        "—",
    )
    standing_row = next((r for r in standings if r["club_id"] == club["id"]), None)

    pts = standing_row["points"] if standing_row else 0
    wins = standing_row["wins"] if standing_row else 0
    draws = standing_row["draws"] if standing_row else 0
    losses = standing_row["losses"] if standing_row else 0
    gf = standing_row["gf"] if standing_row else 0
    ga = standing_row["ga"] if standing_row else 0

    top_players = players[:5]
    player_lines = "\n".join(
        f"  {POSITION_EMOJI.get(p['position'], '⚽')} {p['name']} ({p['position']}, OVR {p['ovr']})"
        for p in top_players
    )

    avg_ovr = sum(p["ovr"] for p in players) // max(len(players), 1)
    budget_m = club["budget"] // 1_000_000

    text = (
        f"🏟 <b>{club['name']}</b>\n"
        f"{'─' * 30}\n"
        f"💰 Бюджет: <b>{budget_m}M€</b>\n"
        f"⭐ Средний OVR: <b>{avg_ovr}</b>\n"
        f"👥 Игроков в составе: <b>{len(players)}</b>\n"
        f"🏆 Трофеев: <b>{club.get('trophies', 0)}</b>\n\n"
        f"📊 Позиция в таблице: <b>#{position}</b>\n"
        f"Сыграно: {wins}В {draws}Н {losses}П | {gf}:{ga} | {pts} очков\n\n"
        f"🌟 <b>Лучшие игроки:</b>\n{player_lines}\n\n"
        f"<i>Используйте /club_info {club['name']} для полного состава</i>"
    )
    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /club_info <name>
# ---------------------------------------------------------------------------
@router.message(Command("club_info"))
async def cmd_club_info(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("ℹ️ Использование: /club_info <название клуба>")
        return

    search_name = args[1].strip()

    async with get_db() as db:
        clubs = await get_all_clubs(db)
        club = next(
            (c for c in clubs if search_name.lower() in c["name"].lower()),
            None,
        )
        if not club:
            await message.answer(f"❌ Клуб «{search_name}» не найден.")
            return

        players = await get_players(db, club["id"])
        season = await get_current_season(db)
        standings = await get_standings(db, season)

    position = next(
        (i + 1 for i, r in enumerate(standings) if r["club_id"] == club["id"]),
        "—",
    )
    standing_row = next((r for r in standings if r["club_id"] == club["id"]), None)

    # Format players by position group
    gks = [p for p in players if p["position"] == "GK"]
    defenders = [p for p in players if p["position"] in ("CB", "LB", "RB")]
    midfielders = [p for p in players if p["position"] in ("CDM", "CM", "CAM")]
    forwards = [p for p in players if p["position"] in ("LW", "RW", "ST")]
    utility = [p for p in players if p["position"] == "Utility"]

    def _fmt_group(group: list, label: str) -> str:
        if not group:
            return ""
        lines = [f"\n<b>{label}</b>"]
        for p in group:
            inj = " 🤕" if p.get("injury_rounds", 0) > 0 else ""
            sus = " 🟥" if p.get("suspension_rounds", 0) > 0 else ""
            lines.append(
                f"  {POSITION_EMOJI.get(p['position'], '⚽')} {p['name']} "
                f"| OVR {p['ovr']} | Возраст {p['age']}"
                f" | ⚽{p.get('goals', 0)} 🅰️{p.get('assists', 0)}{inj}{sus}"
            )
        return "\n".join(lines)

    budget_m = club["budget"] // 1_000_000
    avg_ovr = sum(p["ovr"] for p in players) // max(len(players), 1)
    pts = standing_row["points"] if standing_row else 0

    owner_info = "Нет менеджера" if not club.get("owner_id") else f"ID: {club['owner_id']}"

    text = (
        f"🏟 <b>{club['name']} ({club.get('short_name', '?')})</b>\n"
        f"{'─' * 32}\n"
        f"👨‍💼 Менеджер: {owner_info}\n"
        f"💰 Бюджет: {budget_m}M€  |  ⭐ OVR: {avg_ovr}\n"
        f"📊 Позиция: #{position}  |  Очков: {pts}\n"
        f"{'─' * 32}"
        f"{_fmt_group(gks, '🧤 Вратари')}"
        f"{_fmt_group(defenders, '🛡 Защитники')}"
        f"{_fmt_group(midfielders, '⚙️ Полузащитники')}"
        f"{_fmt_group(forwards, '⚽ Нападающие')}"
        f"{_fmt_group(utility, '🔄 Универсалы')}"
    )

    # Telegram has 4096 char limit
    if len(text) > 4000:
        text = text[:3990] + "\n…"

    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /player_info <name>
# ---------------------------------------------------------------------------
@router.message(Command("player_info"))
async def cmd_player_info(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("ℹ️ Использование: /player_info <имя игрока>")
        return

    name = args[1].strip()
    async with get_db() as db:
        player = await get_player_by_name(db, name)
        if not player:
            await message.answer(f"❌ Игрок «{name}» не найден.")
            return
        club = await get_club(db, player["club_id"])

    club_name = club["name"] if club else "Свободный агент"
    pos_emoji = POSITION_EMOJI.get(player["position"], "⚽")
    inj_text = f"🤕 Травма ({player['injury_rounds']} туров)" if player.get("injury_rounds", 0) > 0 else "✅ Здоров"
    sus_text = f"🟥 Дисквалификация ({player['suspension_rounds']} туров)" if player.get("suspension_rounds", 0) > 0 else ""

    text = (
        f"{pos_emoji} <b>{player['name']}</b>\n"
        f"{'─' * 28}\n"
        f"🏟 Клуб: <b>{club_name}</b>\n"
        f"📍 Позиция: <b>{player['position']}</b>\n"
        f"🎂 Возраст: <b>{player['age']}</b>\n"
        f"⭐ OVR: <b>{player['ovr']}</b>\n\n"
        f"📈 <b>Статистика сезона:</b>\n"
        f"⚽ Голи: {player.get('goals', 0)}\n"
        f"🅰️ Передачи: {player.get('assists', 0)}\n"
        f"🟨 Жёлтых карточек: {player.get('yellow_cards', 0)}\n"
        f"🟥 Красных карточек: {player.get('red_cards', 0)}\n\n"
        f"💊 Статус: {inj_text}"
        f"{chr(10) + sus_text if sus_text else ''}"
    )
    await message.answer(text, parse_mode="HTML")


# ---------------------------------------------------------------------------
# /register_club (admin reassign)
# ---------------------------------------------------------------------------
@router.message(Command("register_club"))
async def cmd_register_club(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов.")
        return
    await message.answer(
        "🔧 Используйте /claim_club для назначения клуба пользователю.\n"
        "Или напишите /panel для полной панели управления."
    )


# ---------------------------------------------------------------------------
# /set_sponsor <tier>
# ---------------------------------------------------------------------------
@router.message(Command("set_sponsor"))
async def cmd_set_sponsor(message: Message):
    async with get_db() as db:
        manager = await get_manager(db, message.from_user.id)
        if not manager:
            await message.answer("⚽ Сначала выберите клуб: /claim_club")
            return

        args = message.text.split(maxsplit=1)
        tier = args[1].strip() if len(args) > 1 else "medium"

        sponsor_data = {
            "low":    ("Localia Sports", 15, 2_000_000, "low"),
            "medium": ("LaLiga Partners", 8, 5_000_000, "medium"),
            "high":   ("Emirates Premium", 4, 12_000_000, "high"),
        }.get(tier, ("Unnamed Sponsor", 10, 1_000_000, "low"))

        sponsor_name, target_pos, bonus, risk = sponsor_data
        club_id = manager["club_id"]

        await db.execute(
            """INSERT INTO sponsor_contracts (club_id, sponsor_name, target_position, bonus_amount, risk_level)
               VALUES (?,?,?,?,?)
               ON CONFLICT(club_id) DO UPDATE SET
                 sponsor_name=excluded.sponsor_name,
                 target_position=excluded.target_position,
                 bonus_amount=excluded.bonus_amount,
                 risk_level=excluded.risk_level""",
            (club_id, sponsor_name, target_pos, bonus, risk),
        )
        await db.commit()

    bonus_m = bonus // 1_000_000
    await message.answer(
        f"✅ <b>Спонсорский контракт заключён!</b>\n\n"
        f"🏢 Спонсор: {sponsor_name}\n"
        f"🎯 Целевая позиция: Топ-{target_pos}\n"
        f"💰 Бонус при выполнении: {bonus_m}M€\n"
        f"⚠️ Уровень риска: {risk}",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /backup
# ---------------------------------------------------------------------------
@router.message(Command("backup"))
async def cmd_backup(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов.")
        return
    if os.path.exists(DB_PATH):
        doc = FSInputFile(DB_PATH, filename="laliga_dynasty_backup.db")
        await message.answer_document(doc, caption="💾 Резервная копия базы данных La Liga Dynasty")
    else:
        await message.answer("❌ Файл базы данных не найден.")


# ---------------------------------------------------------------------------
# /reset_season command
# ---------------------------------------------------------------------------
@router.message(Command("reset_season"))
async def cmd_reset_season(message: Message):
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов.")
        return

    await message.answer(
        "⚠️ <b>Внимание! Сброс сезона</b>\n\n"
        "Это действие сбросит лигу в <b>Сезон 1, Тур 1</b>, восстановит стартовые бюджеты клубов, "
        "очистит статистику матчей, турнирную таблицу и сгенерирует новый календарь игр.\n\n"
        "Вы уверены?",
        parse_mode="HTML",
        reply_markup=get_reset_season_confirm_keyboard(),
    )


# ---------------------------------------------------------------------------
# /offer_sponsors (admin only)
# ---------------------------------------------------------------------------
@router.message(Command("offer_sponsors"))
async def cmd_offer_sponsors(message: Message):
    """Admin command to send sponsor offers to all clubs."""
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Только для администраторов.")
        return

    from database import generate_sponsor_offers

    async with get_db() as db:
        clubs = await get_all_clubs(db)
        total_offers = 0
        for club in clubs:
            offers = await generate_sponsor_offers(db, club["id"])
            total_offers += len(offers)

    await message.answer(
        f"✅ <b>Спонсорские предложения разосланы!</b>\n\n"
        f"Сгенерировано {total_offers} предложений для {len(clubs)} клубов.\n"
        f"Тренеры могут просмотреть и принять контракт через команду /sponsors!",
        parse_mode="HTML",
    )

    try:
        await message.bot.send_message(
            GROUP_CHAT_ID,
            "🏢 <b>Внимание тренерам клубов!</b>\n\n"
            "Крупные коммерческие компании и спонсоры прислали официальные предложения о сотрудничестве!\n"
            "Ознакомьтесь с условиями и подпишите контракт: /sponsors ✍️",
            parse_mode="HTML",
        )
    except Exception:
        pass
