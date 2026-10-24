"""
handlers/matchday.py — La Liga AI Dynasty
Matchday simulation, pre-match preparation, Copa del Rey draw.
All user-facing text is in Russian.
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import aiosqlite
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, GROUP_CHAT_ID
from database import (
    apply_match_result,
    get_club,
    get_club_by_owner,
    get_current_round,
    get_current_season,
    get_db,
    get_players,
    update_club_budget,
    update_game_state,
)
from ai_service import simulate_match, simulate_multiple_matches
from keyboards import (
    get_captain_keyboard,
    get_lategame_keyboard,
    get_prematch_menu_keyboard,
    get_simulation_mode_keyboard,
    get_tactics_keyboard,
)

router = Router()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# {match_id: {"full": int, "fast": int, "voters": set()}}
simulation_votes: dict[int, dict] = {}

# {club_id: {"captain_id": int|None, "tactic": str|None, "late_plan": str|None}}
prematch_state: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Locale helpers
# ---------------------------------------------------------------------------

TACTIC_NAMES_RU = {
    "bus": "Автобус (Парк)",
    "press": "Тотальный прессинг",
    "tiki": "Тики-така",
    "direct": "Бей-беги",
}

LATE_NAMES_RU = {
    "attack": "Все в атаку",
    "defend": "Сушим игру",
    "counter": "Контратаки",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _fmt_budget(amount: float) -> str:
    m = amount / 1_000_000
    return f"{m:.1f}M€" if m != int(m) else f"{int(m)}M€"


def _get_prematch(club_id: int) -> dict:
    if club_id not in prematch_state:
        prematch_state[club_id] = {
            "captain_id": None,
            "tactic": None,
            "late_plan": None,
        }
    return prematch_state[club_id]


def _avg_ovr(players: list[dict]) -> float:
    if not players:
        return 60.0
    return sum(p["ovr"] for p in players) / len(players)


def _build_team_dict(
    club: dict,
    players: list[dict],
    state: dict,
    all_players: list[dict],
) -> dict:
    """Build the team dict consumed by ai_service.simulate_match."""
    available = [p for p in players if p.get("injury_rounds", 0) == 0 and p.get("suspension_rounds", 0) == 0]
    injured = [p["name"] for p in players if p.get("injury_rounds", 0) > 0]
    suspended = [p["name"] for p in players if p.get("suspension_rounds", 0) > 0]

    captain_name = None
    if state.get("captain_id"):
        for p in all_players:
            if p["id"] == state["captain_id"]:
                captain_name = p["name"]
                break

    return {
        "name": club["name"],
        "avg_ovr": round(_avg_ovr(available), 1),
        "tactic": state.get("tactic") or "tiki",
        "late_plan": state.get("late_plan") or "defend",
        "captain": captain_name or (available[0]["name"] if available else ""),
        "players": [
            {"name": p["name"], "position": p["position"], "ovr": p["ovr"]}
            for p in available
        ],
        "injured_players": injured,
        "suspended_players": suspended,
    }


def _build_match_report(
    match_data: dict,
    home_name: str,
    away_name: str,
    result: Any,
    current_round: int,
    season: int,
) -> str:
    """Format the match result as a Russian telegram message."""
    home_goals = result.home_goals if hasattr(result, "home_goals") else result.get("home_goals", 0)
    away_goals = result.away_goals if hasattr(result, "away_goals") else result.get("away_goals", 0)

    # Goals narrative
    scorers_raw = result.scorers if hasattr(result, "scorers") else result.get("scorers", [])
    home_scorers = [s for s in scorers_raw if s.get("club") in (home_name, "home") or s.get("side") == "home"]
    away_scorers = [s for s in scorers_raw if s.get("club") in (away_name, "away") or s.get("side") == "away"]

    def _scorer_lines(scorer_list: list[dict]) -> str:
        lines = []
        for s in scorer_list:
            name = s.get("name") or s.get("player_name", "?")
            minute = s.get("minute", "?")
            lines.append(f"    • {name} — {minute}'")
        return "\n".join(lines) if lines else "    —"

    # Cards
    yellows_raw = result.yellow_cards if hasattr(result, "yellow_cards") else result.get("yellow_cards", [])
    reds_raw = result.red_cards if hasattr(result, "red_cards") else result.get("red_cards", [])
    injuries_raw = result.injuries if hasattr(result, "injuries") else result.get("injuries", [])

    def _names(lst: list) -> str:
        names = [item.get("name") or item.get("player_name", "?") for item in lst]
        return ", ".join(names) if names else "нет"

    # Stats
    pos_home = result.possession_home if hasattr(result, "possession_home") else result.get("possession_home", 50)
    pos_away = 100 - pos_home
    shots_home = result.shots_home if hasattr(result, "shots_home") else result.get("shots_home", 0)
    shots_away = result.shots_away if hasattr(result, "shots_away") else result.get("shots_away", 0)
    mvp = result.mvp if hasattr(result, "mvp") else result.get("mvp", "—")
    narrative = result.narrative if hasattr(result, "narrative") else result.get("narrative", "")

    return (
        f"⚽ <b>ИТОГ МАТЧА</b> | Тур {current_round} | Сезон {season}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏟️ <b>{home_name}</b> vs <b>{away_name}</b>\n"
        f"📊 Счёт: <b>{home_goals} — {away_goals}</b>\n\n"
        f"⚽ <b>Голы:</b>\n"
        f"  {home_name}:\n{_scorer_lines(home_scorers)}\n"
        f"  {away_name}:\n{_scorer_lines(away_scorers)}\n\n"
        f"🟨 Жёлтые: {_names(yellows_raw)}\n"
        f"🟥 Красные: {_names(reds_raw)}\n"
        f"🏥 Травмы: {_names(injuries_raw)}\n\n"
        f"📈 <b>Статистика:</b>\n"
        f"  Владение: {pos_home}% — {pos_away}%\n"
        f"  Удары в створ: {shots_home} — {shots_away}\n\n"
        f"🌟 MVP: <b>{mvp}</b>\n\n"
        f"📝 <b>Репортаж:</b>\n{narrative}"
    )


def _apply_sponsor_bonus(club: dict, home_goals: int, away_goals: int, is_home: bool) -> float:
    """Return sponsor bonus/penalty based on match result and contract type."""
    if is_home:
        won = home_goals > away_goals
        drew = home_goals == away_goals
    else:
        won = away_goals > home_goals
        drew = away_goals == home_goals

    contract = club.get("sponsor_contract", "safe")
    if contract == "safe":
        if won:
            return 2_000_000.0
        if drew:
            return 1_000_000.0
        return 0.0
    else:  # risky
        if won:
            return 5_000_000.0
        if drew:
            return 0.0
        return -1_000_000.0


def _match_revenue(home_goals: int, away_goals: int) -> tuple[float, float]:
    """Return (home_bonus, away_bonus) for TV + result money."""
    tv = 2_000_000.0
    if home_goals > away_goals:
        home_bonus = tv + 3_000_000.0
        away_bonus = tv + 300_000.0
    elif home_goals < away_goals:
        home_bonus = tv + 300_000.0
        away_bonus = tv + 3_000_000.0
    else:
        home_bonus = tv + 1_000_000.0
        away_bonus = tv + 1_000_000.0
    return home_bonus, away_bonus


# ---------------------------------------------------------------------------
# /prematch <match_id>
# ---------------------------------------------------------------------------

@router.message(Command("prematch"))
async def cmd_prematch(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer(
            "❌ Использование: /prematch &lt;match_id&gt;",
            parse_mode="HTML",
        )
        return

    try:
        match_id = int(parts[1])
    except ValueError:
        await message.answer("❌ match_id должен быть целым числом.")
        return

    user_id = message.from_user.id

    async with await get_db() as db:
        async with db.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ) as cur:
            match_row = await cur.fetchone()

        if match_row is None:
            await message.answer(f"❌ Матч с ID {match_id} не найден.")
            return

        match = dict(match_row)

        if match["status"] == "completed":
            await message.answer("⚠️ Этот матч уже завершён.")
            return

        # Verify user owns one of the clubs
        home_club = await get_club(db, match["home_club_id"])
        away_club = await get_club(db, match["away_club_id"])

        is_home_owner = home_club and home_club.get("owner_id") == user_id
        is_away_owner = away_club and away_club.get("owner_id") == user_id

        if not (is_home_owner or is_away_owner or _is_admin(user_id)):
            await message.answer("⛔ Вы не являетесь владельцем клуба в этом матче.")
            return

        club_id = match["home_club_id"] if is_home_owner else match["away_club_id"]
        if _is_admin(user_id) and not (is_home_owner or is_away_owner):
            club_id = match["home_club_id"]

        club = await get_club(db, club_id)

    _get_prematch(club_id)  # Initialise state
    keyboard = get_prematch_menu_keyboard(club_id)

    season = match.get("season", "?")
    rnd = match.get("round", "?")
    home_name = home_club["name"] if home_club else "?"
    away_name = away_club["name"] if away_club else "?"

    await message.answer(
        f"⚽ <b>ПРЕДМАТЧЕВОЕ МЕНЮ</b>\n\n"
        f"🏟️ {home_name} vs {away_name}\n"
        f"📅 Сезон {season} | Тур {rnd}\n\n"
        f"👑 Клуб: <b>{club['name']}</b>\n"
        f"Выберите настройки перед матчем:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# /simulate_match <match_id> — admin only
# ---------------------------------------------------------------------------

@router.message(Command("simulate_match"))
async def cmd_simulate_match(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return

    parts = message.text.split()
    if len(parts) != 2:
        await message.answer(
            "❌ Использование: /simulate_match &lt;match_id&gt;",
            parse_mode="HTML",
        )
        return

    try:
        match_id = int(parts[1])
    except ValueError:
        await message.answer("❌ match_id должен быть целым числом.")
        return

    await message.answer(f"⏳ Симулируем матч #{match_id}...")

    async with await get_db() as db:
        async with db.execute(
            "SELECT * FROM matches WHERE id = ?", (match_id,)
        ) as cur:
            match_row = await cur.fetchone()

        if match_row is None:
            await message.answer(f"❌ Матч с ID {match_id} не найден.")
            return

        match = dict(match_row)

        if match["status"] == "completed":
            await message.answer("⚠️ Этот матч уже завершён.")
            return

        season = match["season"]
        current_round = match["round"]
        home_club_id = match["home_club_id"]
        away_club_id = match["away_club_id"]

        home_club = await get_club(db, home_club_id)
        away_club = await get_club(db, away_club_id)
        home_players_all = await get_players(db, home_club_id)
        away_players_all = await get_players(db, away_club_id)

        # Build team dicts
        home_state = _get_prematch(home_club_id)
        away_state = _get_prematch(away_club_id)

        # Override tactics from DB if set
        if match.get("tactics_home"):
            home_state["tactic"] = match["tactics_home"]
        if match.get("tactics_away"):
            away_state["tactic"] = match["tactics_away"]

        home_team = _build_team_dict(home_club, home_players_all, home_state, home_players_all)
        away_team = _build_team_dict(away_club, away_players_all, away_state, away_players_all)

        # Determine simulation mode from votes
        votes = simulation_votes.get(match_id, {"full": 0, "fast": 0, "voters": set()})
        fast_mode = votes["fast"] > votes["full"]

        # Simulate
        result = await simulate_match(home_team, away_team, fast_mode)

        # Normalise result to dict
        if hasattr(result, "__dict__"):
            res = result
            home_goals = getattr(res, "home_goals", 0)
            away_goals = getattr(res, "away_goals", 0)
            scorers = getattr(res, "scorers", [])
            yellow_cards = getattr(res, "yellow_cards", [])
            red_cards = getattr(res, "red_cards", [])
            injuries = getattr(res, "injuries", [])
            narrative = getattr(res, "narrative", "")
            possession_home = getattr(res, "possession_home", 50)
            shots_home = getattr(res, "shots_home", 0)
            shots_away = getattr(res, "shots_away", 0)
            mvp = getattr(res, "mvp", "—")
        else:
            res = result
            home_goals = res.get("home_goals", 0)
            away_goals = res.get("away_goals", 0)
            scorers = res.get("scorers", [])
            yellow_cards = res.get("yellow_cards", [])
            red_cards = res.get("red_cards", [])
            injuries = res.get("injuries", [])
            narrative = res.get("narrative", "")
            possession_home = res.get("possession_home", 50)
            shots_home = res.get("shots_home", 0)
            shots_away = res.get("shots_away", 0)
            mvp = res.get("mvp", "—")

        # Apply match result
        await apply_match_result(
            db,
            match_id=match_id,
            home_goals=home_goals,
            away_goals=away_goals,
            scorers=scorers,
            yellow_cards=yellow_cards,
            red_cards=red_cards,
            injuries=injuries,
            narrative=narrative,
            possession_home=possession_home,
            shots_home=shots_home,
            shots_away=shots_away,
            mvp=mvp,
        )

        # Apply matchday revenue
        home_revenue, away_revenue = _match_revenue(home_goals, away_goals)
        await update_club_budget(db, home_club_id, home_revenue)
        await update_club_budget(db, away_club_id, away_revenue)

        # Apply sponsor bonuses
        home_sponsor = _apply_sponsor_bonus(home_club, home_goals, away_goals, is_home=True)
        away_sponsor = _apply_sponsor_bonus(away_club, home_goals, away_goals, is_home=False)
        if home_sponsor != 0:
            await update_club_budget(db, home_club_id, home_sponsor)
        if away_sponsor != 0:
            await update_club_budget(db, away_club_id, away_sponsor)

    # Build and send report
    report = _build_match_report(
        match, home_club["name"], away_club["name"],
        result, current_round, season
    )

    bot = message.bot
    try:
        await bot.send_message(GROUP_CHAT_ID, report, parse_mode="HTML")
    except Exception:
        pass

    await message.answer(
        f"✅ Матч #{match_id} завершён!\n"
        f"📊 Счёт: {home_goals}:{away_goals}\n"
        f"💰 TV-доходы распределены.",
    )

    # Clean up votes
    simulation_votes.pop(match_id, None)


# ---------------------------------------------------------------------------
# simulate_entire_current_round — Core Round Engine
# ---------------------------------------------------------------------------

async def simulate_entire_current_round(bot: Any) -> tuple[int, str]:
    """
    Simulates all pending matches in the current round.
    Used by /simulate_round, /panel button, and autopilot background worker.
    """
    async with await get_db() as db:
        season = await get_current_season(db)
        current_round = await get_current_round(db)

        # Get all pending matches for this round
        async with db.execute(
            """
            SELECT m.*, 
                   hc.name AS home_name, hc.owner_id AS home_owner,
                   ac.name AS away_name, ac.owner_id AS away_owner
            FROM matches m
            JOIN clubs hc ON hc.id = m.home_club_id
            JOIN clubs ac ON ac.id = m.away_club_id
            WHERE m.season = ? AND m.round = ? AND m.status = 'pending'
            ORDER BY m.id
            """,
            (season, current_round),
        ) as cur:
            pending_matches = [dict(r) for r in await cur.fetchall()]

        if not pending_matches:
            return 0, f"⚠️ Нет незавершённых матчей в туре {current_round} сезона {season}."

        human_matches = [
            m for m in pending_matches
            if m.get("home_owner") is not None or m.get("away_owner") is not None
        ]
        ai_matches = [
            m for m in pending_matches
            if m.get("home_owner") is None and m.get("away_owner") is None
        ]

        results_summary = []

        # Simulate human-involved matches one by one
        for match in human_matches:
            home_club = await get_club(db, match["home_club_id"])
            away_club = await get_club(db, match["away_club_id"])
            home_players = await get_players(db, match["home_club_id"])
            away_players = await get_players(db, match["away_club_id"])

            home_state = _get_prematch(match["home_club_id"])
            away_state = _get_prematch(match["away_club_id"])

            if match.get("tactics_home"):
                home_state["tactic"] = match["tactics_home"]
            if match.get("tactics_away"):
                away_state["tactic"] = match["tactics_away"]

            home_team = _build_team_dict(home_club, home_players, home_state, home_players)
            away_team = _build_team_dict(away_club, away_players, away_state, away_players)

            votes = simulation_votes.get(match["id"], {"full": 0, "fast": 0, "voters": set()})
            fast_mode = votes["fast"] > votes["full"]

            result = await simulate_match(home_team, away_team, fast_mode)

            hg = getattr(result, "home_goals", 0) if hasattr(result, "home_goals") else result.get("home_goals", 0)
            ag = getattr(result, "away_goals", 0) if hasattr(result, "away_goals") else result.get("away_goals", 0)
            scorers = getattr(result, "scorers", []) if hasattr(result, "scorers") else (result.get("scorers", []) if isinstance(result, dict) else [])
            yellow_cards = getattr(result, "yellow_cards", []) if hasattr(result, "yellow_cards") else result.get("yellow_cards", [])
            red_cards = getattr(result, "red_cards", []) if hasattr(result, "red_cards") else result.get("red_cards", [])
            injuries = getattr(result, "injuries", []) if hasattr(result, "injuries") else result.get("injuries", [])
            narrative = getattr(result, "narrative_report", "") if hasattr(result, "narrative_report") else result.get("narrative_report", "")
            possession_home = getattr(result, "possession_home", 50) if hasattr(result, "possession_home") else result.get("possession_home", 50)
            shots_home = getattr(result, "shots_on_target_home", 0) if hasattr(result, "shots_on_target_home") else result.get("shots_on_target_home", 0)
            shots_away = getattr(result, "shots_on_target_away", 0) if hasattr(result, "shots_on_target_away") else result.get("shots_on_target_away", 0)
            mvp = getattr(result, "mvp", "—") if hasattr(result, "mvp") else result.get("mvp", "—")

            await apply_match_result(
                db,
                match_id=match["id"],
                home_goals=hg,
                away_goals=ag,
                scorers=scorers,
                yellow_cards=yellow_cards,
                red_cards=red_cards,
                injuries=injuries,
                narrative=narrative,
                possession_home=possession_home,
                shots_home=shots_home,
                shots_away=shots_away,
                mvp=mvp,
            )

            home_revenue, away_revenue = _match_revenue(hg, ag)
            await update_club_budget(db, match["home_club_id"], home_revenue)
            await update_club_budget(db, match["away_club_id"], away_revenue)

            home_sponsor = _apply_sponsor_bonus(home_club, hg, ag, is_home=True)
            away_sponsor = _apply_sponsor_bonus(away_club, hg, ag, is_home=False)
            if home_sponsor != 0:
                await update_club_budget(db, match["home_club_id"], home_sponsor)
            if away_sponsor != 0:
                await update_club_budget(db, match["away_club_id"], away_sponsor)

            results_summary.append((match["home_name"], match["away_name"], hg, ag))

            report = _build_match_report(
                match, match["home_name"], match["away_name"],
                result, current_round, season
            )
            try:
                await bot.send_message(GROUP_CHAT_ID, report, parse_mode="HTML")
            except Exception:
                pass

            simulation_votes.pop(match["id"], None)

        # Simulate AI-only matches in batch
        if ai_matches:
            ai_team_pairs = []
            for match in ai_matches:
                home_club = await get_club(db, match["home_club_id"])
                away_club = await get_club(db, match["away_club_id"])
                home_players = await get_players(db, match["home_club_id"])
                away_players = await get_players(db, match["away_club_id"])

                home_state = _get_prematch(match["home_club_id"])
                away_state = _get_prematch(match["away_club_id"])

                home_team = _build_team_dict(home_club, home_players, home_state, home_players)
                away_team = _build_team_dict(away_club, away_players, away_state, away_players)
                ai_team_pairs.append((match, home_club, away_club, home_team, away_team))

            batch_inputs = [
                {"home_team": ht, "away_team": at, "fast_mode": True}
                for _, _, _, ht, at in ai_team_pairs
            ]

            ai_results = await simulate_multiple_matches(batch_inputs)

            for (match, home_club, away_club, home_team, away_team), result in zip(
                ai_team_pairs, ai_results
            ):
                hg = getattr(result, "home_goals", 0) if hasattr(result, "home_goals") else result.get("home_goals", 0)
                ag = getattr(result, "away_goals", 0) if hasattr(result, "away_goals") else result.get("away_goals", 0)
                scorers = getattr(result, "scorers", []) if hasattr(result, "scorers") else (result.get("scorers", []) if isinstance(result, dict) else [])
                yellow_cards = getattr(result, "yellow_cards", []) if hasattr(result, "yellow_cards") else result.get("yellow_cards", [])
                red_cards = getattr(result, "red_cards", []) if hasattr(result, "red_cards") else result.get("red_cards", [])
                injuries = getattr(result, "injuries", []) if hasattr(result, "injuries") else result.get("injuries", [])
                narrative = getattr(result, "narrative_report", "") if hasattr(result, "narrative_report") else result.get("narrative_report", "")
                possession_home = getattr(result, "possession_home", 50) if hasattr(result, "possession_home") else result.get("possession_home", 50)
                shots_home = getattr(result, "shots_on_target_home", 0) if hasattr(result, "shots_on_target_home") else result.get("shots_on_target_home", 0)
                shots_away = getattr(result, "shots_on_target_away", 0) if hasattr(result, "shots_on_target_away") else result.get("shots_on_target_away", 0)
                mvp = getattr(result, "mvp", "—") if hasattr(result, "mvp") else result.get("mvp", "—")

                await apply_match_result(
                    db,
                    match_id=match["id"],
                    home_goals=hg,
                    away_goals=ag,
                    scorers=scorers,
                    yellow_cards=yellow_cards,
                    red_cards=red_cards,
                    injuries=injuries,
                    narrative=narrative,
                    possession_home=possession_home,
                    shots_home=shots_home,
                    shots_away=shots_away,
                    mvp=mvp,
                )

                home_revenue, away_revenue = _match_revenue(hg, ag)
                await update_club_budget(db, match["home_club_id"], home_revenue)
                await update_club_budget(db, match["away_club_id"], away_revenue)

                home_sponsor = _apply_sponsor_bonus(home_club, hg, ag, is_home=True)
                away_sponsor = _apply_sponsor_bonus(away_club, hg, ag, is_home=False)
                if home_sponsor != 0:
                    await update_club_budget(db, match["home_club_id"], home_sponsor)
                if away_sponsor != 0:
                    await update_club_budget(db, match["away_club_id"], away_sponsor)

                results_summary.append((match["home_name"], match["away_name"], hg, ag))

        # Advance round counter
        await update_game_state(db, "current_round", current_round + 1)

    # Post round summary to group
    summary_lines = [
        f"📋 <b>ИТОГИ ТУРА {current_round} | Сезон {season}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for home_name, away_name, hg, ag in results_summary:
        summary_lines.append(f"  🏟️ {home_name} {hg}:{ag} {away_name}")

    summary_lines.append(f"\n➡️ Переходим к туру {current_round + 1}!")
    summary_text = "\n".join(summary_lines)

    try:
        await bot.send_message(GROUP_CHAT_ID, summary_text, parse_mode="HTML")
    except Exception:
        pass

    return len(results_summary), summary_text


# ---------------------------------------------------------------------------
# /simulate_round — admin only
# ---------------------------------------------------------------------------

@router.message(Command("simulate_round"))
async def cmd_simulate_round(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return

    await message.answer("⏳ Симулируем все матчи тура...")
    count, summary = await simulate_entire_current_round(message.bot)
    if count == 0:
        await message.answer(summary)
    else:
        await message.answer(
            f"✅ Тур завершён! Сыграно матчей: {count}.\n"
            f"Отчёт опубликован в группе.",
        )



# ---------------------------------------------------------------------------
# /copa_draw — admin only
# ---------------------------------------------------------------------------

@router.message(Command("copa_draw"))
async def cmd_copa_draw(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("⛔ Эта команда доступна только администраторам.")
        return

    async with await get_db() as db:
        season = await get_current_season(db)

        # Check if already drawn
        async with db.execute(
            "SELECT COUNT(*) AS cnt FROM copa_bracket WHERE season = ?", (season,)
        ) as cur:
            row = dict(await cur.fetchone())
            if row["cnt"] > 0:
                await message.answer(
                    f"⚠️ Жеребьёвка Кубка дель Рей для сезона {season} уже проводилась."
                )
                return

        async with db.execute("SELECT id, name FROM clubs ORDER BY id") as cur:
            clubs = [dict(r) for r in await cur.fetchall()]

    if len(clubs) < 4:
        await message.answer("❌ Недостаточно клубов для жеребьёвки Кубка.")
        return

    # Shuffle clubs randomly
    random.shuffle(clubs)

    # Copa structure: 20 teams
    # R32: 16 teams play → 8 winners
    # 4 seeded teams get bye to R16
    # We'll put 4 top seeds last (they get byes), remaining 16 play R32
    seeded = clubs[:4]    # 4 teams with bye to R16
    rest = clubs[4:]      # 16 teams play R32

    lines = [
        f"🏆 <b>ЖЕРЕБЬЁВКА КУБКА ДЕЛЬ РЕЙ</b> | Сезон {season}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🌟 <b>Сеяные (выход в 1/8 финала):</b>",
    ]
    for club in seeded:
        lines.append(f"  ⭐ {club['name']}")

    lines.append("")
    lines.append("⚔️ <b>1/16 финала:</b>")

    async with await get_db() as db:
        for i in range(0, len(rest), 2):
            c1 = rest[i]
            c2 = rest[i + 1] if i + 1 < len(rest) else seeded[0]  # fallback
            await db.execute(
                """
                INSERT INTO copa_bracket (season, round_name, club1_id, club2_id)
                VALUES (?, '1/16 финала', ?, ?)
                """,
                (season, c1["id"], c2["id"]),
            )
            lines.append(f"  ⚔️ {c1['name']} vs {c2['name']}")

        lines.append("")
        lines.append("🏅 <b>Сеяные ждут в 1/8 финала:</b>")
        for club in seeded:
            lines.append(f"  ✅ {club['name']}")

        await db.commit()

    text = "\n".join(lines)

    try:
        await message.bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception:
        pass

    await message.answer(
        f"✅ Жеребьёвка Кубка дель Рей сезона {season} проведена!\n"
        f"4 сеяных получили бай в 1/8, {len(rest) // 2} матчей в 1/16.",
    )


# ---------------------------------------------------------------------------
# Callback: prematch_captain:{club_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("prematch_captain:"))
async def cb_prematch_captain(callback: CallbackQuery) -> None:
    try:
        club_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    async with await get_db() as db:
        club = await get_club(db, club_id)
        if club is None:
            await callback.answer("Клуб не найден.", show_alert=True)
            return

        players = await get_players(db, club_id)

    available = [p for p in players if p.get("injury_rounds", 0) == 0 and p.get("suspension_rounds", 0) == 0]

    if not available:
        await callback.answer("Нет доступных игроков для выбора капитана!", show_alert=True)
        return

    keyboard = get_captain_keyboard(club_id, available)
    await callback.message.edit_text(
        f"👑 <b>Выбор капитана | {club['name']}</b>\n\n"
        "Выберите капитана на этот матч:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Callback: captain:{club_id}:{player_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("captain:"))
async def cb_captain(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        club_id = int(parts[1])
        player_id = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    async with await get_db() as db:
        async with db.execute(
            "SELECT name FROM players WHERE id = ?", (player_id,)
        ) as cur:
            row = await cur.fetchone()

    if row is None:
        await callback.answer("Игрок не найден.", show_alert=True)
        return

    player_name = row["name"]
    state = _get_prematch(club_id)
    state["captain_id"] = player_id

    await callback.answer(f"👑 Капитан выбран: {player_name}", show_alert=True)
    keyboard = get_prematch_menu_keyboard(club_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Callback: prematch_tactic:{club_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("prematch_tactic:"))
async def cb_prematch_tactic(callback: CallbackQuery) -> None:
    try:
        club_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    async with await get_db() as db:
        club = await get_club(db, club_id)

    name = club["name"] if club else "Клуб"
    keyboard = get_tactics_keyboard(club_id)
    await callback.message.edit_text(
        f"⚽ <b>Выбор тактики | {name}</b>\n\n"
        "Выберите тактическую схему на матч:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Callback: tactic:{club_id}:{style}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("tactic:"))
async def cb_tactic(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        club_id = int(parts[1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    style = parts[2]
    style_ru = TACTIC_NAMES_RU.get(style, style)

    state = _get_prematch(club_id)
    state["tactic"] = style

    # Save to pending match if found
    async with await get_db() as db:
        season = await get_current_season(db)
        current_round = await get_current_round(db)
        async with db.execute(
            """
            SELECT id FROM matches
            WHERE season = ? AND round = ? AND status = 'pending'
            AND (home_club_id = ? OR away_club_id = ?)
            LIMIT 1
            """,
            (season, current_round, club_id, club_id),
        ) as cur:
            match_row = await cur.fetchone()

        if match_row:
            match_id = match_row["id"]
            async with db.execute(
                "SELECT home_club_id FROM matches WHERE id = ?", (match_id,)
            ) as cur:
                m = await cur.fetchone()

            if m and m["home_club_id"] == club_id:
                await db.execute(
                    "UPDATE matches SET tactics_home = ? WHERE id = ?",
                    (style, match_id),
                )
            else:
                await db.execute(
                    "UPDATE matches SET tactics_away = ? WHERE id = ?",
                    (style, match_id),
                )
            await db.commit()

    await callback.answer(f"⚽ Тактика: {style_ru}", show_alert=True)
    keyboard = get_prematch_menu_keyboard(club_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Callback: prematch_late:{club_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("prematch_late:"))
async def cb_prematch_late(callback: CallbackQuery) -> None:
    try:
        club_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    async with await get_db() as db:
        club = await get_club(db, club_id)

    name = club["name"] if club else "Клуб"
    keyboard = get_lategame_keyboard(club_id)
    await callback.message.edit_text(
        f"🎯 <b>План на концовку | {name}</b>\n\n"
        "Выберите план на последние 20 минут матча:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Callback: late:{club_id}:{plan}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("late:"))
async def cb_late(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        club_id = int(parts[1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    plan = parts[2]
    plan_ru = LATE_NAMES_RU.get(plan, plan)

    state = _get_prematch(club_id)
    state["late_plan"] = plan

    await callback.answer(f"🎯 План на концовку: {plan_ru}", show_alert=True)
    keyboard = get_prematch_menu_keyboard(club_id)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Callback: prematch_simmode:{club_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("prematch_simmode:"))
async def cb_prematch_simmode(callback: CallbackQuery) -> None:
    try:
        club_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    async with await get_db() as db:
        season = await get_current_season(db)
        current_round = await get_current_round(db)
        club = await get_club(db, club_id)

        async with db.execute(
            """
            SELECT id FROM matches
            WHERE season = ? AND round = ? AND status = 'pending'
            AND (home_club_id = ? OR away_club_id = ?)
            LIMIT 1
            """,
            (season, current_round, club_id, club_id),
        ) as cur:
            match_row = await cur.fetchone()

    if match_row is None:
        await callback.answer("Матч не найден.", show_alert=True)
        return

    match_id = match_row["id"]
    name = club["name"] if club else "Клуб"
    keyboard = get_simulation_mode_keyboard(match_id)

    await callback.message.edit_text(
        f"🗳️ <b>Режим симуляции | {name}</b>\n\n"
        "Проголосуйте за режим симуляции матча:\n\n"
        "🎬 <b>Полный репортаж</b> — подробное описание матча\n"
        "⚡ <b>Быстрый результат</b> — только счёт",
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    await callback.answer()


# ---------------------------------------------------------------------------
# Callback: simmode:{match_id}:{mode}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("simmode:"))
async def cb_simmode(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    try:
        match_id = int(parts[1])
    except ValueError:
        await callback.answer("Ошибка данных.", show_alert=True)
        return

    mode = parts[2]  # "full" or "fast"
    user_id = callback.from_user.id

    if match_id not in simulation_votes:
        simulation_votes[match_id] = {"full": 0, "fast": 0, "voters": set()}

    vote_data = simulation_votes[match_id]

    if user_id in vote_data["voters"]:
        await callback.answer("⚠️ Вы уже проголосовали!", show_alert=True)
        return

    vote_data["voters"].add(user_id)
    if mode == "fast":
        vote_data["fast"] += 1
        mode_ru = "⚡ Быстрый результат"
    else:
        vote_data["full"] += 1
        mode_ru = "🎬 Полный репортаж"

    total = vote_data["full"] + vote_data["fast"]
    await callback.answer(
        f"✅ Ваш голос: {mode_ru}\n"
        f"Голосов: Полный {vote_data['full']} | Быстрый {vote_data['fast']} (всего {total})",
        show_alert=True,
    )
