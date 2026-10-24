"""handlers/training.py — Training sessions."""
from __future__ import annotations

import json
import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from config import ADMIN_IDS
from database import (
    get_db,
    get_club,
    get_club_by_owner,
    get_players,
    get_current_season,
    get_current_round,
)
from ai_service import generate_training_report
from keyboards import get_training_keyboard

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map callback focus key → Russian display name and emoji
FOCUS_MAP = {
    "attack":   ("Атака",              "⚽"),
    "defense":  ("Оборона",            "🛡️"),
    "fitness":  ("Физподготовка",      "💪"),
    "academy":  ("Академия молодёжи",  "🌱"),
}

# Map callback focus key → AI service focus string
FOCUS_TO_AI = {
    "attack":  "Атака",
    "defense": "Оборона",
    "fitness": "Физика",
    "academy": "Академия молодежи",
}

# XP threshold to gain 1 OVR point: base 100 + 50 per OVR above 70
def _ovr_threshold(current_ovr: int) -> int:
    extra = max(0, current_ovr - 70) * 50
    return 100 + extra


# ---------------------------------------------------------------------------
# /train
# ---------------------------------------------------------------------------

@router.message(Command("train"))
async def cmd_train(message: Message) -> None:
    """Show training options for the current round if not already trained."""
    caller_id = message.from_user.id

    async with await get_db() as db:
        club = await get_club_by_owner(db, caller_id)
        if club is None:
            await message.reply("❌ У вас нет клуба. Сначала зарегистрируйтесь как менеджер.")
            return

        season = await get_current_season(db)
        round_ = await get_current_round(db)

        # Check if already trained this round
        async with db.execute(
            """SELECT id FROM training_sessions
               WHERE club_id = ? AND season = ? AND round = ?""",
            (club["id"], season, round_),
        ) as cur:
            existing = await cur.fetchone()

    if existing:
        await message.reply(
            f"⚠️ Вы уже провели тренировку в этом туре (Сезон {season}, Тур {round_}).\n"
            f"Следующая тренировка будет доступна после тура {round_ + 1}."
        )
        return

    keyboard = get_training_keyboard(club["id"])
    await message.reply(
        f"🏋️ *Тренировка — «{club['name']}»*\n"
        f"📅 Сезон {season} | Тур {round_}\n\n"
        f"Выберите направление тренировки:",
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback: train:{club_id}:{focus}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("train:"))
async def cb_train(callback: CallbackQuery) -> None:
    """Execute a training session for the selected focus area."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("❌ Неверные данные.", show_alert=True)
        return

    _, club_id_str, focus = parts
    club_id = int(club_id_str)
    caller_id = callback.from_user.id

    if focus not in FOCUS_MAP:
        await callback.answer("❌ Неизвестное направление тренировки.", show_alert=True)
        return

    focus_name, focus_emoji = FOCUS_MAP[focus]
    ai_focus = FOCUS_TO_AI[focus]

    async with await get_db() as db:
        # Verify caller owns this club
        caller_club = await get_club_by_owner(db, caller_id)
        if caller_club is None or caller_club["id"] != club_id:
            await callback.answer("❌ Это не ваш клуб.", show_alert=True)
            return

        club = await get_club(db, club_id)
        season = await get_current_season(db)
        round_ = await get_current_round(db)

        # Double-check: not already trained this round
        async with db.execute(
            """SELECT id FROM training_sessions
               WHERE club_id = ? AND season = ? AND round = ?""",
            (club_id, season, round_),
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            await callback.answer(
                "⚠️ Вы уже провели тренировку в этом туре.", show_alert=True
            )
            return

        # Load active, non-injured players
        all_players = await get_players(db, club_id, include_retired=False)
        active_players = [
            p for p in all_players
            if not p.get("is_retired") and p.get("injury_rounds", 0) == 0
        ]

        if not active_players:
            await callback.answer(
                "❌ Нет доступных игроков для тренировки.", show_alert=True
            )
            return

    await callback.answer("⏳ Тренировка проводится...", show_alert=False)

    # Generate training report via AI
    try:
        report = await generate_training_report(club["name"], ai_focus, active_players)
    except Exception as exc:
        logger.exception("generate_training_report failed")
        await callback.message.reply(f"❌ Ошибка генерации отчёта тренировки: {exc}")
        return

    # ---------------------------------------------------------------------------
    # Apply XP gains and OVR upgrades
    # ---------------------------------------------------------------------------
    progress_lines: list[str] = []
    xp_gained_map: dict[str, int] = {}

    # Build a lookup from player name → player dict for active players
    player_lookup: dict[str, dict] = {p["name"]: p for p in active_players}

    async with await get_db() as db:
        for improvement in report.player_improvements:
            p_name: str = improvement.get("player_name", "")
            xp_gain: int = improvement.get("xp_gain", 0)
            ovr_gain_reported: int = improvement.get("ovr_gain", 0)

            player = player_lookup.get(p_name)
            if player is None:
                # AI may have returned a slightly different name; skip safely
                continue

            # Apply 2x XP multiplier for under-21 players (code-level enforcement)
            actual_xp = xp_gain * 2 if player["age"] < 21 else xp_gain
            xp_gained_map[p_name] = actual_xp

            # Fetch current player state fresh from DB for accurate XP/OVR
            async with db.execute(
                "SELECT xp, ovr FROM players WHERE id = ?", (player["id"],)
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                continue

            current_xp = row["xp"] + actual_xp
            current_ovr = row["ovr"]

            # Check for OVR upgrade(s)
            upgrades = 0
            while current_xp >= _ovr_threshold(current_ovr) and current_ovr < 99:
                current_xp -= _ovr_threshold(current_ovr)
                current_ovr += 1
                upgrades += 1

            await db.execute(
                "UPDATE players SET xp = ?, ovr = ? WHERE id = ?",
                (current_xp, current_ovr, player["id"]),
            )

            old_ovr = player["ovr"]
            if upgrades > 0:
                progress_lines.append(
                    f"• {p_name} +{actual_xp}XP 🆙 OVR {old_ovr}→{current_ovr}!"
                )
                logger.info(
                    "OVR upgrade: %s (%d→%d), club_id=%d", p_name, old_ovr, current_ovr, club_id
                )
            else:
                progress_lines.append(f"• {p_name} +{actual_xp}XP")

        # Apply injury if triggered
        injury_note = ""
        if report.injury_risk_triggered and report.injured_player:
            injured_name = report.injured_player
            await db.execute(
                """UPDATE players SET injury_rounds = injury_rounds + 1
                   WHERE name = ? AND club_id = ?""",
                (injured_name, club_id),
            )
            injury_note = f"\n⚠️ {injured_name} получил лёгкую травму (1 тур)"

        # Record training session
        await db.execute(
            """INSERT INTO training_sessions (club_id, season, round, focus, report, xp_gained_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                club_id,
                season,
                round_,
                ai_focus,
                report.report_text,
                json.dumps(xp_gained_map),
            ),
        )
        await db.commit()

    # ---------------------------------------------------------------------------
    # Build and send formatted report message
    # ---------------------------------------------------------------------------
    progress_text = "\n".join(progress_lines) if progress_lines else "Нет данных."

    text = (
        f"🏋️ *ТРЕНИРОВКА — {club['name'].upper()} | {focus_emoji} {focus_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"{report.report_text}\n\n"
        f"📈 *Прогресс игроков:*\n"
        f"{progress_text}"
        f"{injury_note}"
    )

    try:
        await callback.message.edit_text(text, parse_mode="Markdown")
    except Exception:
        await callback.message.reply(text, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /training_history
# ---------------------------------------------------------------------------

@router.message(Command("training_history"))
async def cmd_training_history(message: Message) -> None:
    """Show the last 5 training sessions for the caller's club."""
    caller_id = message.from_user.id

    async with await get_db() as db:
        club = await get_club_by_owner(db, caller_id)
        if club is None:
            await message.reply("❌ У вас нет клуба.")
            return

        async with db.execute(
            """SELECT season, round, focus, report, xp_gained_json, timestamp
               FROM training_sessions
               WHERE club_id = ?
               ORDER BY id DESC
               LIMIT 5""",
            (club["id"],),
        ) as cur:
            sessions = await cur.fetchall()

    if not sessions:
        await message.reply(f"📋 У клуба «{club['name']}» ещё нет тренировок.")
        return

    FOCUS_RU = {
        "Атака": "⚽ Атака",
        "Оборона": "🛡️ Оборона",
        "Физика": "💪 Физподготовка",
        "Академия молодежи": "🌱 Академия",
    }

    lines = [f"📚 *История тренировок — «{club['name']}»*\n━━━━━━━━━━━━━━━━━━━━━"]
    for s in sessions:
        focus_display = FOCUS_RU.get(s["focus"], s["focus"])
        # Summarise XP totals from JSON
        try:
            xp_map: dict = json.loads(s["xp_gained_json"] or "{}")
            total_xp = sum(xp_map.values())
            players_trained = len(xp_map)
            xp_summary = f"Итого XP: +{total_xp} ({players_trained} игроков)"
        except Exception:
            xp_summary = ""

        report_preview = (s["report"] or "")[:120].strip()
        if len(s["report"] or "") > 120:
            report_preview += "..."


        lines.append(
            f"\n📅 *Сезон {s['season']}, Тур {s['round']}* — {focus_display}\n"
            f"_{report_preview}_\n"
            f"📊 {xp_summary}"
        )

    await message.reply("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# League-wide Training (Zero-Touch & Master Panel)
# ---------------------------------------------------------------------------

async def run_training_for_all_clubs(db: aiosqlite.Connection, focus: str | None = None) -> dict[str, Any]:
    """
    Run weekly training across all 20 clubs in La Liga simultaneously.
    Called via master Telegram panel and autopilot worker.
    """
    import random
    season = await get_current_season(db)
    round_ = await get_current_round(db)

    async with db.execute("SELECT * FROM clubs ORDER BY id") as cur:
        clubs = [dict(r) for r in await cur.fetchall()]

    foci = ["Атака", "Оборона", "Физика", "Академия молодежи"]
    upgrades_count = 0
    total_xp_awarded = 0

    for club in clubs:
        club_focus = focus or random.choice(foci)
        players = await get_players(db, club["id"], include_retired=False)
        active = [p for p in players if p.get("injury_rounds", 0) == 0]

        xp_map = {}
        for p in active:
            is_u21 = p["age"] < 21
            mult = 2 if is_u21 else 1
            xp_gain = random.randint(20, 50) * mult
            xp_map[p["name"]] = xp_gain
            total_xp_awarded += xp_gain

            curr_xp = p["xp"] + xp_gain
            curr_ovr = p["ovr"]
            req_xp = _ovr_threshold(curr_ovr)

            if curr_xp >= req_xp and curr_ovr < 99:
                curr_xp -= req_xp
                curr_ovr += 1
                upgrades_count += 1

            await db.execute(
                "UPDATE players SET xp = ?, ovr = ? WHERE id = ?",
                (curr_xp, curr_ovr, p["id"]),
            )

        await db.execute(
            """INSERT INTO training_sessions (club_id, season, round, focus, report, xp_gained_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                club["id"],
                season,
                round_,
                club_focus,
                f"Недельная тренировка клуба «{club['name']}» по направлению {club_focus}.",
                json.dumps(xp_map),
            ),
        )

    await db.commit()
    return {
        "season": season,
        "round": round_,
        "clubs_trained": len(clubs),
        "upgrades_count": upgrades_count,
        "total_xp_awarded": total_xp_awarded,
    }

