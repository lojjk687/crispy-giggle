"""handlers/matchday.py — Match simulation and pre-match setup handlers."""

import json
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import (
    ADMIN_IDS,
    GROUP_CHAT_ID,
    MATCH_DRAW_REVENUE,
    MATCH_LOSS_REVENUE,
    MATCH_WIN_REVENUE,
    RED_CARD_FINE,
    TV_RIGHTS_PER_ROUND,
)
from database import (
    apply_match_result,
    get_all_clubs,
    get_club,
    get_current_round,
    get_current_season,
    get_db,
    get_manager,
    get_players,
    get_club_by_owner,
    get_game_state,
    update_club_budget,
    update_game_state,
)
from keyboards import (
    get_captain_keyboard,
    get_friendly_response_keyboard,
    get_lategame_keyboard,
    get_prematch_menu_keyboard,
    get_simulation_mode_keyboard,
    get_tactics_keyboard,
    TACTICS,
)

router = Router()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper: build team context dict for AI
# ---------------------------------------------------------------------------

async def _build_team_context(db, club: dict, tactics: str = "4-4-2") -> dict:
    players = await get_players(db, club["id"])
    avg_ovr = sum(p["ovr"] for p in players) // max(len(players), 1)
    players_str = ", ".join(
        f"{p['name']} ({p['position']}, OVR {p['ovr']})"
        for p in players[:11]
    )
    return {
        "name": club["name"],
        "avg_ovr": avg_ovr,
        "tactics": tactics,
        "players_str": players_str,
        "club_id": club["id"],
    }


# ---------------------------------------------------------------------------
# /prematch <match_id>
# ---------------------------------------------------------------------------

@router.message(Command("prematch"))
async def cmd_prematch(message: Message):
    """Show pre-match setup menu."""
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        # Show upcoming matches
        async with get_db() as db:
            manager = await get_manager(db, message.from_user.id)
            if not manager:
                await message.answer("⚽ Сначала выберите клуб: /claim_club")
                return

            season = await get_current_season(db)
            rnd = await get_current_round(db)
            club_id = manager["club_id"]

            async with db.execute(
                """SELECT m.*, ch.name as home_name, ca.name as away_name
                   FROM matches m
                   JOIN clubs ch ON ch.id=m.home_club_id
                   JOIN clubs ca ON ca.id=m.away_club_id
                   WHERE m.season=? AND m.round=? AND m.status='scheduled'
                     AND (m.home_club_id=? OR m.away_club_id=?)""",
                (season, rnd, club_id, club_id),
            ) as cur:
                match = await cur.fetchone()

        if not match:
            await message.answer(
                f"📅 Нет запланированных матчей в туре {rnd}.\n"
                f"Возможно, тур уже сыгран или матч ещё не назначен."
            )
            return

        match = dict(match)
        await _show_prematch_menu(message, match)
        return

    try:
        match_id = int(args[1])
    except ValueError:
        await message.answer("❌ Неверный ID матча.")
        return

    async with get_db() as db:
        async with db.execute(
            """SELECT m.*, ch.name as home_name, ca.name as away_name
               FROM matches m
               JOIN clubs ch ON ch.id=m.home_club_id
               JOIN clubs ca ON ca.id=m.away_club_id
               WHERE m.id=?""",
            (match_id,),
        ) as cur:
            match = await cur.fetchone()

    if not match:
        await message.answer(f"❌ Матч #{match_id} не найден.")
        return

    await _show_prematch_menu(message, dict(match))


async def _show_prematch_menu(message: Message, match: dict):
    match_id = match["id"]
    text = (
        f"⚽ <b>Предматчевое меню</b>\n\n"
        f"🏟 {match.get('home_name', '?')} vs {match.get('away_name', '?')}\n"
        f"📅 Сезон {match['season']}, Тур {match['round']}\n\n"
        f"Настройте команду перед матчем:"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_prematch_menu_keyboard(match_id))


# ---------------------------------------------------------------------------
# Pre-match callbacks
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("prematch_tactic:"))
async def cb_prematch_tactic(callback: CallbackQuery):
    match_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "📋 Выберите тактику:",
        reply_markup=get_tactics_keyboard(match_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prematch_captain:"))
async def cb_prematch_captain(callback: CallbackQuery):
    match_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with get_db() as db:
        manager = await get_manager(db, user_id)
        if not manager:
            await callback.answer("❌ У вас нет клуба!", show_alert=True)
            return
        players = await get_players(db, manager["club_id"])

    # Show top players as captain candidates
    top = sorted(players, key=lambda p: p["ovr"], reverse=True)[:10]
    await callback.message.answer(
        "©️ Выберите капитана:",
        reply_markup=get_captain_keyboard(match_id, top),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prematch_lategame:"))
async def cb_prematch_lategame(callback: CallbackQuery):
    match_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "⚡ Выберите план на концовку матча:",
        reply_markup=get_lategame_keyboard(match_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prematch_simmode:"))
async def cb_prematch_simmode(callback: CallbackQuery):
    match_id = int(callback.data.split(":")[1])
    await callback.message.answer(
        "🕹 Выберите режим симуляции:",
        reply_markup=get_simulation_mode_keyboard(match_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prematch_go:"))
async def cb_prematch_go(callback: CallbackQuery):
    match_id = int(callback.data.split(":")[1])
    await callback.answer("▶️ Запускаю матч…")
    await _simulate_one_match(callback.message, match_id, fast_mode=False)


# ---------------------------------------------------------------------------
# Tactic / Captain / Lategame / SimMode selections
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("tactic:"))
async def cb_tactic(callback: CallbackQuery):
    parts = callback.data.split(":")
    match_id = int(parts[1])
    tactic = parts[2]
    user_id = callback.from_user.id

    async with get_db() as db:
        manager = await get_manager(db, user_id)
        if not manager:
            await callback.answer("❌ У вас нет клуба!", show_alert=True)
            return
        club_id = manager["club_id"]

        # Update tactics for home or away
        async with db.execute(
            "SELECT home_club_id, away_club_id FROM matches WHERE id=?",
            (match_id,),
        ) as cur:
            match = await cur.fetchone()

        if not match:
            await callback.answer("❌ Матч не найден!", show_alert=True)
            return

        if match["home_club_id"] == club_id:
            await db.execute("UPDATE matches SET tactics_home=? WHERE id=?", (tactic, match_id))
        elif match["away_club_id"] == club_id:
            await db.execute("UPDATE matches SET tactics_away=? WHERE id=?", (tactic, match_id))
        else:
            await callback.answer("❌ Это не ваш матч!", show_alert=True)
            return
        await db.commit()

    await callback.answer(f"✅ Тактика {tactic} выбрана!", show_alert=True)


@router.callback_query(F.data.startswith("captain:"))
async def cb_captain(callback: CallbackQuery):
    parts = callback.data.split(":", 2)
    match_id = int(parts[1])
    captain_name = parts[2]
    user_id = callback.from_user.id

    async with get_db() as db:
        manager = await get_manager(db, user_id)
        if not manager:
            await callback.answer("❌ У вас нет клуба!", show_alert=True)
            return
        club_id = manager["club_id"]

        async with db.execute(
            "SELECT home_club_id, away_club_id FROM matches WHERE id=?",
            (match_id,),
        ) as cur:
            match = await cur.fetchone()

        if not match:
            await callback.answer("❌ Матч не найден!", show_alert=True)
            return

        if match["home_club_id"] == club_id:
            await db.execute("UPDATE matches SET captain_home=? WHERE id=?", (captain_name, match_id))
        elif match["away_club_id"] == club_id:
            await db.execute("UPDATE matches SET captain_away=? WHERE id=?", (captain_name, match_id))
        else:
            await callback.answer("❌ Это не ваш матч!", show_alert=True)
            return
        await db.commit()

    await callback.answer(f"©️ Капитан {captain_name} назначен!", show_alert=True)


@router.callback_query(F.data.startswith("simmode:"))
async def cb_simmode(callback: CallbackQuery):
    parts = callback.data.split(":")
    match_id = int(parts[1])
    mode = parts[2]  # fast or detailed
    fast = mode == "fast"
    await callback.answer(f"🕹 Режим «{'Быстрый' if fast else 'Детальный'}» выбран!")
    await _simulate_one_match(callback.message, match_id, fast_mode=fast)


# ---------------------------------------------------------------------------
# Core simulation logic
# ---------------------------------------------------------------------------

async def _simulate_one_match(message: Message, match_id: int, fast_mode: bool = True):
    """Simulate a single match, persist results, and post report."""
    async with get_db() as db:
        async with db.execute(
            """SELECT m.*, ch.name as home_name, ca.name as away_name
               FROM matches m
               JOIN clubs ch ON ch.id=m.home_club_id
               JOIN clubs ca ON ca.id=m.away_club_id
               WHERE m.id=?""",
            (match_id,),
        ) as cur:
            match = await cur.fetchone()

        if not match:
            await message.answer(f"❌ Матч #{match_id} не найден.")
            return

        match = dict(match)
        if match["status"] == "played":
            await message.answer(f"⚠️ Матч #{match_id} уже сыгран!")
            return

        home_club = await get_club(db, match["home_club_id"])
        away_club = await get_club(db, match["away_club_id"])

        if not home_club or not away_club:
            await message.answer("❌ Один из клубов не найден.")
            return

        home_ctx = await _build_team_context(db, home_club, match.get("tactics_home", "4-4-2"))
        away_ctx = await _build_team_context(db, away_club, match.get("tactics_away", "4-4-2"))

    await message.answer(
        f"⚽ <b>Симуляция матча</b>\n"
        f"{match['home_name']} 🆚 {match['away_name']}\n"
        f"⏳ Идёт симуляция…",
        parse_mode="HTML",
    )

    # AI simulation
    from ai_service import simulate_match as ai_simulate
    result = await ai_simulate(home_ctx, away_ctx, fast_mode=fast_mode)

    # Extract fields using getattr ONLY (never .get() on Pydantic models)
    hg = getattr(result, "home_goals", 0)
    ag = getattr(result, "away_goals", 0)
    scorers = getattr(result, "scorers", [])          # uses @property
    yellow_cards = getattr(result, "yellow_cards", [])  # uses @property
    red_cards = getattr(result, "red_cards", [])        # uses @property
    injuries = getattr(result, "injuries", [])           # uses @property
    narrative = getattr(result, "narrative_report", "")
    possession_home = getattr(result, "possession_home", 50)
    shots_home = getattr(result, "shots_on_target_home", 4)
    shots_away = getattr(result, "shots_on_target_away", 4)
    mvp = getattr(result, "mvp", "—")

    # Persist result
    async with get_db() as db:
        await apply_match_result(
            db, match_id, hg, ag,
            scorers, yellow_cards, red_cards, injuries,
            narrative, possession_home, shots_home, shots_away, mvp,
        )

        # Apply match revenues
        home_id = match["home_club_id"]
        away_id = match["away_club_id"]

        # TV rights for both clubs
        await update_club_budget(db, home_id, TV_RIGHTS_PER_ROUND)
        await update_club_budget(db, away_id, TV_RIGHTS_PER_ROUND)

        # Match result revenue
        if hg > ag:
            await update_club_budget(db, home_id, MATCH_WIN_REVENUE)
            await update_club_budget(db, away_id, MATCH_LOSS_REVENUE)
        elif hg == ag:
            await update_club_budget(db, home_id, MATCH_DRAW_REVENUE)
            await update_club_budget(db, away_id, MATCH_DRAW_REVENUE)
        else:
            await update_club_budget(db, home_id, MATCH_LOSS_REVENUE)
            await update_club_budget(db, away_id, MATCH_WIN_REVENUE)

        # Red card fines
        home_reds = len(getattr(result, "red_cards_home", []))
        away_reds = len(getattr(result, "red_cards_away", []))
        if home_reds > 0:
            await update_club_budget(db, home_id, -RED_CARD_FINE * home_reds)
        if away_reds > 0:
            await update_club_budget(db, away_id, -RED_CARD_FINE * away_reds)

    # Format result message
    text = _format_match_report(match, hg, ag, scorers, yellow_cards, red_cards, injuries, mvp, narrative, possession_home, shots_home, shots_away)

    await message.answer(text, parse_mode="HTML")

    # Post to group
    try:
        await message.bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Group post failed: {e}")


def _format_match_report(
    match: dict, hg: int, ag: int,
    scorers: list, yellow_cards: list, red_cards: list, injuries: list,
    mvp: str, narrative: str, poss_home: int, sh: int, sa: int,
) -> str:
    home_name = match.get("home_name", "Хозяева")
    away_name = match.get("away_name", "Гости")
    poss_away = 100 - poss_home

    result_line = "🏆 Победа хозяев!" if hg > ag else ("🤝 Ничья!" if hg == ag else "🏆 Победа гостей!")

    # Scorers
    scorers_home = [s for s in scorers if s.get("side") == "home"]
    scorers_away = [s for s in scorers if s.get("side") == "away"]
    home_scorer_str = " | ".join(f"⚽{s.get('name','?')} {s.get('minute','?')}'" for s in scorers_home) or "—"
    away_scorer_str = " | ".join(f"⚽{s.get('name','?')} {s.get('minute','?')}'" for s in scorers_away) or "—"

    # Cards
    yc_str = " | ".join(f"🟨{c.get('name','?')}" for c in yellow_cards) or "—"
    rc_str = " | ".join(f"🟥{c.get('name','?')}" for c in red_cards) if red_cards else ""
    inj_str = " | ".join(f"🤕{i.get('name','?')} ({i.get('rounds_out',1)} тура)" for i in injuries) if injuries else ""

    text = (
        f"{'=' * 32}\n"
        f"⚽ <b>ФИНАЛЬНЫЙ СВИСТОК!</b>\n"
        f"🏟 <b>{home_name}</b>  {hg} : {ag}  <b>{away_name}</b>\n"
        f"{result_line}\n"
        f"{'=' * 32}\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"🏃 Владение: {poss_home}% — {poss_away}%\n"
        f"🎯 Удары в цель: {sh} — {sa}\n\n"
        f"<b>Голы {home_name}:</b>\n{home_scorer_str}\n"
        f"<b>Голы {away_name}:</b>\n{away_scorer_str}\n"
    )

    if yc_str != "—":
        text += f"\n🟨 Жёлтые карточки: {yc_str}\n"
    if rc_str:
        text += f"🟥 Красные карточки: {rc_str}\n"
    if inj_str:
        text += f"🤕 Травмы: {inj_str}\n"

    text += f"\n🌟 <b>MVP матча:</b> {mvp}\n\n"
    if narrative:
        text += f"📰 <b>Репортаж:</b>\n<i>{narrative}</i>"

    return text


# ---------------------------------------------------------------------------
# /simulate_match <match_id>
# ---------------------------------------------------------------------------

@router.message(Command("simulate_match"))
async def cmd_simulate_match(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("ℹ️ Использование: /simulate_match <match_id>")
        return

    try:
        match_id = int(args[1])
    except ValueError:
        await message.answer("❌ Неверный ID матча.")
        return

    await _simulate_one_match(message, match_id, fast_mode=False)


# ---------------------------------------------------------------------------
# /simulate_round
# ---------------------------------------------------------------------------

@router.message(Command("simulate_round"))
async def cmd_simulate_round(message: Message):
    """Simulate all unplayed matches in the current round."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return

    async with get_db() as db:
        # Check international break
        intl = await get_game_state(db, "intl_break", "off")
        if intl == "on":
            await message.answer(
                "🌍 <b>Внимание: Сейчас действует перерыв на матчи сборных!</b>\n\n"
                "Матчи клубов Ла Лиги приостановлены.\n"
                "Чтобы завершить перерыв, администратор может использовать /international_break.",
                parse_mode="HTML",
            )
            return

        season = await get_current_season(db)
        rnd = await get_current_round(db)

        async with db.execute(
            """SELECT m.id FROM matches m
               WHERE m.season=? AND m.round=? AND m.status='scheduled'""",
            (season, rnd),
        ) as cur:
            match_rows = await cur.fetchall()

    match_ids = [r["id"] for r in match_rows]
    if not match_ids:
        await message.answer(
            f"✅ Все матчи тура {rnd} (сезон {season}) уже сыграны!\n"
            f"Используйте /next_round для перехода к следующему туру."
        )
        return

    await message.answer(
        f"🏟 <b>Симуляция тура {rnd} — Сезон {season}</b>\n"
        f"⏳ Симулирую {len(match_ids)} матчей…",
        parse_mode="HTML",
    )

    for match_id in match_ids:
        await _simulate_one_match(message, match_id, fast_mode=True)

    # Advance round
    async with get_db() as db:
        await update_game_state(db, "round", str(rnd + 1))

    await message.answer(
        f"✅ <b>Тур {rnd} завершён!</b>\n"
        f"📊 Текущий тур: {rnd + 1}\n\n"
        f"Используйте /standings для просмотра таблицы.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /next_round
# ---------------------------------------------------------------------------

@router.message(Command("next_round"))
async def cmd_next_round(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return

    async with get_db() as db:
        rnd = await get_current_round(db)
        await update_game_state(db, "round", str(rnd + 1))

    await message.answer(
        f"✅ Переход к туру <b>{rnd + 1}</b>.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# /set_tactic <formation>
# ---------------------------------------------------------------------------

@router.message(Command("set_tactic"))
async def cmd_set_tactic(message: Message):
    """Set default team formation for future matches."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        valid_tactics = ", ".join(TACTICS.keys())
        await message.answer(
            f"ℹ️ Использование: <code>/set_tactic &lt;схема&gt;</code>\n\n"
            f"Доступные схемы:\n{valid_tactics}\n\n"
            f"Пример: <code>/set_tactic 4-3-3</code>",
            parse_mode="HTML",
        )
        return

    chosen = parts[1].strip()
    if chosen not in TACTICS:
        valid_tactics = ", ".join(TACTICS.keys())
        await message.answer(f"❌ Неизвестная схема «{chosen}».\nДоступные: {valid_tactics}")
        return

    user_id = message.from_user.id
    async with get_db() as db:
        club = await get_club_by_owner(db, user_id)
        if not club:
            await message.answer("❌ У вас нет клуба! Используйте /claim_club.")
            return

        # Store default tactic in game_state or club
        await update_game_state(db, f"club_tactic_{club['id']}", chosen)

    t_info = TACTICS[chosen]
    await message.answer(
        f"✅ {t_info['emoji']} Для клуба <b>{club['name']}</b> установлена тактика: <b>{t_info['name']}</b> ({chosen})!\n"
        f"📝 <i>{t_info['desc']}</i>\n\n"
        f"Она будет автоматически применяться в ваших матчах.",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------------
# Friendly matches: /friendly @username & simulation
# ---------------------------------------------------------------------------

@router.message(Command("friendly"))
async def cmd_friendly(message: Message):
    """Challenge another manager's club to a friendly match."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "ℹ️ Использование: <code>/friendly @соперник</code>\n"
            "Пример: <code>/friendly @rival_coach</code>",
            parse_mode="HTML",
        )
        return

    target_mention = parts[1].strip().lstrip("@")
    challenger_id = message.from_user.id

    async with get_db() as db:
        home_club = await get_club_by_owner(db, challenger_id)
        if not home_club:
            await message.answer("❌ У вас нет клуба! Используйте /claim_club.")
            return

        # Find target manager
        target_manager = None
        async with db.execute("SELECT * FROM managers") as cur:
            all_managers = await cur.fetchall()

        for m in all_managers:
            if m["username"] and target_mention.lower() in m["username"].lower():
                target_manager = dict(m)
                break

        if not target_manager:
            await message.answer(f"❌ Менеджер @{target_mention} не найден в лиге.")
            return

        away_club = await get_club(db, target_manager["club_id"])
        if not away_club:
            await message.answer("❌ Клуб соперника не найден.")
            return

        if away_club["id"] == home_club["id"]:
            await message.answer("❌ Нельзя сыграть товарищеский матч против самого себя.")
            return

        season = await get_current_season(db)

        # Create scheduled friendly match
        async with db.execute(
            """INSERT INTO friendly_matches (season, home_club_id, away_club_id, status)
               VALUES (?,?,?,'pending')""",
            (season, home_club["id"], away_club["id"]),
        ) as cur:
            f_match_id = cur.lastrowid
        await db.commit()

    challenge_text = (
        f"🤝 <b>Вызов на товарищеский матч!</b>\n\n"
        f"🏟 <b>{home_club['name']}</b> бросает вызов клубу <b>{away_club['name']}</b> (@{target_mention})!\n\n"
        f"Менеджер @{target_mention}, вы принимаете спарринг?"
    )

    await message.answer(
        challenge_text,
        parse_mode="HTML",
        reply_markup=get_friendly_response_keyboard(f_match_id),
    )


@router.callback_query(F.data.startswith("friendly_accept:"))
async def cb_friendly_accept(callback: CallbackQuery):
    """Opponent accepts friendly match."""
    f_match_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with get_db() as db:
        async with db.execute(
            """SELECT fm.*, ch.name as home_name, ca.name as away_name,
                      ca.owner_id as away_owner_id, ch.owner_id as home_owner_id
               FROM friendly_matches fm
               JOIN clubs ch ON ch.id=fm.home_club_id
               JOIN clubs ca ON ca.id=fm.away_club_id
               WHERE fm.id=?""",
            (f_match_id,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            await callback.answer("Матч не найден!", show_alert=True)
            return

        f_match = dict(row)
        if f_match["away_owner_id"] != user_id and user_id not in ADMIN_IDS:
            await callback.answer("❌ Только тренер вызванного клуба может принять матч!", show_alert=True)
            return

        await db.execute("UPDATE friendly_matches SET status='accepted' WHERE id=?", (f_match_id,))
        await db.commit()

    await callback.answer("Товарищеский матч принят!")
    await callback.message.edit_text(
        f"✅ <b>Товарищеский матч подтверждён!</b>\n\n"
        f"🏟 <b>{f_match['home_name']}</b> vs <b>{f_match['away_name']}</b>\n\n"
        f"Симулируйте матч командой:\n<code>/simulate_friendly {f_match_id}</code>",
        parse_mode="HTML",
    )


@router.callback_query(F.data.startswith("friendly_decline:"))
async def cb_friendly_decline(callback: CallbackQuery):
    """Opponent declines friendly match."""
    f_match_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    async with get_db() as db:
        await db.execute("UPDATE friendly_matches SET status='declined' WHERE id=?", (f_match_id,))
        await db.commit()

    await callback.answer("Матч отклонён.")
    await callback.message.edit_text("❌ Товарищеский матч был отклонён.")


@router.message(Command("simulate_friendly"))
async def cmd_simulate_friendly(message: Message):
    """Simulate a confirmed friendly match."""
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("ℹ️ Использование: <code>/simulate_friendly &lt;id&gt;</code>", parse_mode="HTML")
        return

    try:
        f_id = int(parts[1])
    except ValueError:
        await message.answer("❌ Неверный ID матча.")
        return

    async with get_db() as db:
        async with db.execute(
            """SELECT fm.*, ch.name as home_name, ca.name as away_name,
                      ch.tier as home_tier, ca.tier as away_tier
               FROM friendly_matches fm
               JOIN clubs ch ON ch.id=fm.home_club_id
               JOIN clubs ca ON ca.id=fm.away_club_id
               WHERE fm.id=?""",
            (f_id,),
        ) as cur:
            row = await cur.fetchone()

        if not row:
            await message.answer("❌ Товарищеский матч не найден.")
            return

        f_match = dict(row)
        home_club = await get_club(db, f_match["home_club_id"])
        away_club = await get_club(db, f_match["away_club_id"])

        home_ctx = await _build_team_context(db, home_club)
        away_ctx = await _build_team_context(db, away_club)

    await message.answer(f"⏳ Симулирую товарищеский матч <b>{home_club['name']}</b> vs <b>{away_club['name']}</b>…", parse_mode="HTML")

    from ai_service import simulate_match
    result = await simulate_match(home_ctx, away_ctx, fast_mode=False)

    hg = getattr(result, "home_goals", 0)
    ag = getattr(result, "away_goals", 0)
    narrative = getattr(result, "narrative_report", "")
    mvp = getattr(result, "mvp", "—")

    async with get_db() as db:
        # Save friendly match result
        await db.execute(
            """UPDATE friendly_matches
               SET home_goals=?, away_goals=?, narrative=?, status='completed'
               WHERE id=?""",
            (hg, ag, narrative, f_id),
        )
        # Give home club 100K gate revenue
        await update_club_budget(db, home_club["id"], 100_000)
        await db.commit()

    report = (
        f"🤝 <b>ИТОГ ТОВАРИЩЕСКОГО МАТЧА</b>\n"
        f"{'═' * 32}\n\n"
        f"🏟 <b>{home_club['name']}</b>  {hg} : {ag}  <b>{away_club['name']}</b>\n\n"
        f"🌟 Лучший игрок: <b>{mvp}</b>\n"
        f"💰 Билетная выручка хозяев: +100 000€\n\n"
        f"📝 <i>{narrative}</i>\n\n"
        f"<i>(Товарищеский матч не влияет на очки и дисквалификации в Ла Лиге)</i>"
    )

    await message.answer(report, parse_mode="HTML")
