"""
handlers/awards.py — Season awards ceremony and autonomous next-season budget engine.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_IDS, GROUP_CHAT_ID, LEAGUE_PRIZES, PRIZE_MONEY
from database import get_db, get_standings, get_current_season, update_club_budget
from ai_service import generate_awards_ceremony, calculate_next_season_budgets, SeasonBudgetReport

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_eur(amount: float) -> str:
    return f"{amount / 1_000_000:.1f}M€"


def _placement_medal(pos: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(pos, f"{pos}.")


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ---------------------------------------------------------------------------
# Pipeline: Awards Ceremony & AI Budget Engine (Upgrade #3)
# ---------------------------------------------------------------------------

async def run_awards_and_budget_pipeline(
    bot: Bot,
    origin_message: Optional[Message] = None,
) -> tuple[dict[str, Any], SeasonBudgetReport]:
    """
    Executes the full awards ceremony, distributes placement prizes and bonuses,
    runs the AI next-season budget engine, updates club budgets in DB,
    and posts formatted reports to GROUP_CHAT_ID.
    """
    async with await get_db() as db:
        season = await get_current_season(db)
        standings_rows = await get_standings(db, season)

        standings: list[dict[str, Any]] = []
        for i, row in enumerate(standings_rows, 1):
            entry = dict(row)
            entry["position"] = i
            entry["club"] = row.get("club_name") or row.get("name") or f"Клуб #{row['club_id']}"
            standings.append(entry)

        if not standings:
            raise ValueError(f"Нет данных турнирной таблицы для сезона {season}.")

        # Top scorers
        async with db.execute(
            """SELECT p.name AS player_name, p.goals, p.assists, c.name AS club
               FROM players p JOIN clubs c ON p.club_id = c.id
               WHERE p.is_retired = 0
               ORDER BY p.goals DESC, p.assists DESC LIMIT 10""",
        ) as cur:
            top_scorers = [dict(r) for r in await cur.fetchall()]

        # All clubs data
        async with db.execute(
            """SELECT c.id, c.name, c.budget, c.tier, c.sponsor_contract, AVG(p.ovr) AS avg_ovr
               FROM clubs c
               LEFT JOIN players p ON p.club_id = c.id AND p.is_retired = 0
               GROUP BY c.id""",
        ) as cur:
            clubs_data = [dict(r) for r in await cur.fetchall()]

        club_name_to_id = {c["name"]: c["id"] for c in clubs_data}
        club_id_to_name = {c["id"]: c["name"] for c in clubs_data}

        # 1. Run AI Awards Ceremony
        ceremony = await generate_awards_ceremony(season, standings, top_scorers, clubs_data)

        # 2. Distribute League Placement Prizes
        prize_summary_lines = []
        for i, standing in enumerate(standings, 1):
            prize = LEAGUE_PRIZES.get(i, 6_000_000.0)
            cid = standing["club_id"]
            await update_club_budget(db, cid, prize)
            cname = club_id_to_name.get(cid, f"#{cid}")
            prize_summary_lines.append(f"{_placement_medal(i)} {cname}: +{_fmt_eur(prize)}")

        # 3. Apply individual trophy bonuses
        async def _apply_bonus(club_name: str, bonus: float, award: str) -> str:
            cid = club_name_to_id.get(club_name)
            if cid is None:
                for name, id_val in club_name_to_id.items():
                    if club_name.lower() in name.lower():
                        cid = id_val
                        break
            if cid:
                await update_club_budget(db, cid, bonus)
                return f"🏅 {award}: «{club_id_to_name.get(cid, club_name)}» +{_fmt_eur(bonus)}"
            return ""

        bonuses_lines = []
        b = await _apply_bonus(ceremony.best_team, 25_000_000, "Чемпион Ла Лиги")
        if b: bonuses_lines.append(b)
        b = await _apply_bonus(ceremony.breakthrough_club, 15_000_000, "Прорыв года")
        if b: bonuses_lines.append(b)
        b = await _apply_bonus(ceremony.bus_master_club, 8_000_000, "Мастер автобуса")
        if b: bonuses_lines.append(b)
        b = await _apply_bonus(ceremony.comeback_king_club, 8_000_000, "Король камбэков")
        if b: bonuses_lines.append(b)
        b = await _apply_bonus(ceremony.rebuilding_grant_club, 12_000_000, "За волю к победе (грант)")
        if b: bonuses_lines.append(b)

        # 4. Run AI Autonomous Next-Season Budget Engine
        awards_summary = {
            "ballon_dor": ceremony.ballon_dor_winner,
            "golden_boy": ceremony.golden_boy_winner,
            "champion": ceremony.best_team,
            "breakthrough": ceremony.breakthrough_club,
        }
        budget_report = await calculate_next_season_budgets(season, standings, awards_summary, clubs_data)

        # Update budgets in database
        new_budgets_map: dict[str, float] = {}
        for ev in budget_report.club_evaluations:
            new_budgets_map[ev.club_name] = ev.new_season_budget
            cid = club_name_to_id.get(ev.club_name)
            if cid:
                await db.execute("UPDATE clubs SET budget = ? WHERE id = ?", (ev.new_season_budget, cid))

        await db.commit()

    # ---------------------------------------------------------------------------
    # Broadcast Ceremony & Financial Report to Telegram
    # ---------------------------------------------------------------------------
    prizes_text = "\n".join(prize_summary_lines[:10])
    msg1 = (
        f"🏆 <b>ЦЕРЕМОНИЯ НАГРАД ЛА ЛИГИ — СЕЗОН {season}</b>\n"
        f"══════════════════════════════════════\n\n"
        f"🎭 {ceremony.ceremony_intro}\n\n"
        f"💶 <b>ПРИЗОВЫЕ ЛИГИ:</b>\n"
        f"{prizes_text}\n"
        f"<i>(все 20 клубов получили выплаты)</i>"
    )
    try:
        await bot.send_message(GROUP_CHAT_ID, msg1, parse_mode="HTML")
    except Exception:
        pass
    await asyncio.sleep(1.2)

    msg2 = (
        f"🌟 <b>ИНДИВИДУАЛЬНЫЕ НАГРАДЫ | СЕЗОН {season}</b>\n"
        f"══════════════════════════════════════\n\n"
        f"🥇 <b>ЗОЛОТОЙ МЯЧ</b>\n"
        f"🏅 <b>{ceremony.ballon_dor_winner}</b>\n"
        f"<i>{ceremony.ballon_dor_text}</i>\n\n"
        f"⭐ <b>GOLDEN BOY (лучший игрок U-21)</b>\n"
        f"🏅 <b>{ceremony.golden_boy_winner}</b>\n"
        f"<i>{ceremony.golden_boy_text}</i>\n\n"
        f"🧤 <b>ТРОФЕЙ ЯШИНА (лучший вратарь)</b>\n"
        f"🏅 <b>{ceremony.best_goalkeeper_winner}</b>\n"
        f"<i>{ceremony.best_goalkeeper_text}</i>"
    )
    try:
        await bot.send_message(GROUP_CHAT_ID, msg2, parse_mode="HTML")
    except Exception:
        pass
    await asyncio.sleep(1.2)

    msg3 = (
        f"⚽ <b>КОМАНДНЫЕ ТРОФЕИ И САМИЗДАТ</b>\n"
        f"══════════════════════════════════════\n\n"
        f"🏆 <b>ЧЕМПИОН ИСПАНИИ: «{ceremony.best_team}»!</b>\n"
        f"<i>{ceremony.best_team_text}</i>\n\n"
        f"👟 <b>Золотая бутса:</b> {ceremony.golden_boot_winner} ({ceremony.golden_boot_goals} голов)\n\n"
        f"🤡 <b>ЗОЛОТАЯ КАЛОША (худший трансфер сезона):</b>\n"
        f"<i>{ceremony.flop_player} — {ceremony.flop_text}</i>"
    )
    try:
        await bot.send_message(GROUP_CHAT_ID, msg3, parse_mode="HTML")
    except Exception:
        pass
    await asyncio.sleep(1.2)

    bonuses_text = "\n".join(bonuses_lines) if bonuses_lines else "—"
    msg4 = (
        f"🎖️ <b>УТЕШИТЕЛЬНЫЕ ПРИЗЫ И БОНУСЫ</b>\n"
        f"══════════════════════════════════════\n\n"
        f"🚀 <b>Прорыв года:</b> «{ceremony.breakthrough_club}»\n<i>{ceremony.breakthrough_text}</i>\n\n"
        f"🧱 <b>Мастер автобуса:</b> «{ceremony.bus_master_club}»\n<i>{ceremony.bus_master_text}</i>\n\n"
        f"⚔️ <b>Король камбэков:</b> «{ceremony.comeback_king_club}»\n<i>{ceremony.comeback_king_text}</i>\n\n"
        f"🩹 <b>За волю к победе (грант аутсайдеру):</b> «{ceremony.rebuilding_grant_club}»\n<i>{ceremony.rebuilding_grant_text}</i>\n\n"
        f"💰 <b>Начисленные бонусы в бюджеты:</b>\n{bonuses_text}"
    )
    try:
        await bot.send_message(GROUP_CHAT_ID, msg4, parse_mode="HTML")
    except Exception:
        pass
    await asyncio.sleep(1.2)

    # Message 5: AI Autonomous Budget Report (Upgrade #3)
    budget_lines = []
    for ev in budget_report.club_evaluations[:10]:
        budget_lines.append(
            f"• <b>{ev.club_name}</b>: <b>{_fmt_eur(ev.new_season_budget)}</b>\n"
            f"   <i>[{ev.sponsor_status}]</i>"
        )
    budget_overview = (
        f"📊 <b>ФИНАНСОВЫЙ ОТЧЁТ И НОВЫЕ БЮДЖЕТЫ НА СЕЗОН {season + 1}</b>\n"
        f"══════════════════════════════════════\n\n"
        f"🏛️ <i>{budget_report.financial_overview}</i>\n\n"
        f"💼 <b>Утверждённые трансферные бюджеты (Топ-10):</b>\n"
        + "\n".join(budget_lines) +
        f"\n\n💰 <i>Все 20 клубов получили бюджеты и готовы к Сезону {season + 1}!</i>"
    )
    try:
        await bot.send_message(GROUP_CHAT_ID, budget_overview, parse_mode="HTML")
    except Exception:
        pass

    return {
        "season": season,
        "champion": ceremony.best_team,
        "ballon_dor": ceremony.ballon_dor_winner,
        "new_budgets": new_budgets_map,
    }, budget_report


# ---------------------------------------------------------------------------
# /awards command (Admin only)
# ---------------------------------------------------------------------------

@router.message(Command("awards"))
async def cmd_awards(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.reply("❌ Только администраторы могут запускать церемонию наград.")
        return

    await message.reply("🎭 Запуск торжественной церемонии наград и расчёта бюджетов...")
    try:
        res, _ = await run_awards_and_budget_pipeline(message.bot, message)
        await message.reply(f"✅ Церемония Сезона {res['season']} и расчёт бюджетов завершены!")
    except Exception as exc:
        logger.exception("Awards pipeline failed")
        await message.reply(f"❌ Ошибка проведения церемонии: {exc}")


# ---------------------------------------------------------------------------
# /season_stats command
# ---------------------------------------------------------------------------

@router.message(Command("season_stats"))
async def cmd_season_stats(message: Message) -> None:
    async with await get_db() as db:
        season = await get_current_season(db)
        round_ = await get_current_round(db)

        async with db.execute(
            "SELECT COUNT(*) AS total_matches, SUM(home_goals + away_goals) AS total_goals FROM matches WHERE season = ? AND status = 'completed'",
            (season,),
        ) as cur:
            row = await cur.fetchone()
            total_matches = row["total_matches"] if row else 0
            total_goals = row["total_goals"] if row and row["total_goals"] else 0

        avg = round(total_goals / total_matches, 2) if total_matches else 0.0

        async with db.execute(
            "SELECT p.name, p.goals, c.name AS club FROM players p JOIN clubs c ON p.club_id = c.id WHERE p.is_retired = 0 ORDER BY p.goals DESC LIMIT 1",
        ) as cur:
            ts_row = await cur.fetchone()
            ts = f"{ts_row['name']} ({ts_row['club']}) — {ts_row['goals']} голов" if ts_row else "—"

    text = (
        f"📊 <b>СТАТИСТИКА ЛА ЛИГИ | СЕЗОН {season}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 Текущий тур: <b>{round_} / 38</b>\n"
        f"⚽ Всего сыграно матчей: <b>{total_matches}</b>\n"
        f"🥅 Забито голов: <b>{total_goals}</b> (в среднем <b>{avg}</b> за матч)\n"
        f"👟 Лучший бомбардир: <b>{ts}</b>\n"
    )
    await message.reply(text, parse_mode="HTML")
