"""handlers/awards.py — Season awards ceremony and next-season pipeline."""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_IDS, GROUP_CHAT_ID, LEAGUE_PRIZES
from database import (
    execute_full_season_rollover,
    get_all_clubs,
    get_current_season,
    get_db,
    get_standings,
    set_club_budget,
    update_club_budget,
)

router = Router()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /awards
# ---------------------------------------------------------------------------

@router.message(Command("awards"))
async def cmd_awards(message: Message):
    """Run the AI end-of-season awards ceremony."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return

    await message.answer("🏆 Подготавливаю церемонию награждения…")

    async with get_db() as db:
        season = await get_current_season(db)
        standings = await get_standings(db, season)
        clubs = await get_all_clubs(db)

        # Build season stats for AI
        top_scorer = None
        top_scorer_goals = 0
        top_scorer_name = "—"

        for club in clubs:
            async with db.execute(
                "SELECT name, goals FROM players WHERE club_id=? AND is_retired=0 ORDER BY goals DESC LIMIT 1",
                (club["id"],),
            ) as cur:
                row = await cur.fetchone()
            if row and row["goals"] > top_scorer_goals:
                top_scorer_goals = row["goals"]
                top_scorer_name = row["name"]
                top_scorer = {"name": row["name"], "club": club["name"], "goals": row["goals"]}

    champion = standings[0]["name"] if standings else "Нет данных"
    champion_id = standings[0]["club_id"] if standings else None

    season_stats = {
        "season": season,
        "champion": champion,
        "top_scorer_name": top_scorer_name,
        "top_scorer_goals": top_scorer_goals,
        "top_scorer": top_scorer,
        "standings_top5": [
            {
                "position": i + 1,
                "club": r["name"],
                "points": r["points"],
                "gf": r["gf"],
                "ga": r["ga"],
            }
            for i, r in enumerate(standings[:5])
        ],
    }

    try:
        from ai_service import run_awards_ceremony
        ceremony = await run_awards_ceremony(season_stats)

        awards_text = (
            f"🏆 <b>{ceremony.ceremony_title}</b>\n"
            f"{'═' * 32}\n\n"
            f"🥇 <b>Лучший игрок:</b> {ceremony.best_player}\n"
            f"🧤 <b>Лучший вратарь:</b> {ceremony.best_goalkeeper}\n"
            f"🌟 <b>Лучший молодой:</b> {ceremony.best_young_player}\n"
            f"⚽ <b>Лучший бомбардир:</b> {ceremony.top_scorer}\n"
            f"👨‍💼 <b>Лучший тренер:</b> {ceremony.best_manager}\n"
        )

        if ceremony.awards:
            awards_text += "\n🎖 <b>Специальные награды:</b>\n"
            for award in ceremony.awards:
                awards_text += f"  • {award.award_name}: {award.player_name} ({award.club_name})\n"

        awards_text += f"\n💬 <i>{ceremony.speech}</i>"

    except Exception as e:
        logger.error(f"Awards ceremony error: {e}")
        awards_text = (
            f"🏆 <b>Итоги сезона {season}</b>\n\n"
            f"🥇 Чемпион: <b>{champion}</b>\n"
            f"⚽ Лучший бомбардир: <b>{top_scorer_name}</b> ({top_scorer_goals} голов)"
        )

    await message.answer(awards_text, parse_mode="HTML")

    # Post to group
    try:
        await message.bot.send_message(GROUP_CHAT_ID, awards_text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Group post failed: {e}")


# ---------------------------------------------------------------------------
# /season_stats
# ---------------------------------------------------------------------------

@router.message(Command("season_stats"))
async def cmd_season_stats(message: Message):
    """Show top statistics leaderboards for the current season."""
    async with get_db() as db:
        season = await get_current_season(db)

        # Top scorers
        async with db.execute(
            """SELECT p.name, p.goals, p.assists, c.name as club_name, p.position
               FROM players p JOIN clubs c ON c.id=p.club_id
               WHERE p.is_retired=0 AND p.goals>0
               ORDER BY p.goals DESC, p.assists DESC
               LIMIT 10""",
        ) as cur:
            top_scorers = await cur.fetchall()

        # Top assisters
        async with db.execute(
            """SELECT p.name, p.assists, c.name as club_name
               FROM players p JOIN clubs c ON c.id=p.club_id
               WHERE p.is_retired=0 AND p.assists>0
               ORDER BY p.assists DESC LIMIT 5""",
        ) as cur:
            top_assists = await cur.fetchall()

        # Most cards
        async with db.execute(
            """SELECT p.name, p.yellow_cards, p.red_cards, c.name as club_name
               FROM players p JOIN clubs c ON c.id=p.club_id
               WHERE p.is_retired=0 AND (p.yellow_cards>0 OR p.red_cards>0)
               ORDER BY (p.yellow_cards + p.red_cards*3) DESC LIMIT 5""",
        ) as cur:
            dirty_players = await cur.fetchall()

    lines = [f"📊 <b>Статистика сезона {season}</b>\n"]

    lines.append("⚽ <b>Бомбардиры:</b>")
    if top_scorers:
        for i, p in enumerate(top_scorers, 1):
            lines.append(f"  {i}. {p['name']} ({p['club_name']}) — {p['goals']} голов, {p['assists']} пас.")
    else:
        lines.append("  Нет данных")

    lines.append("\n🅰️ <b>Ассистенты:</b>")
    if top_assists:
        for i, p in enumerate(top_assists, 1):
            lines.append(f"  {i}. {p['name']} ({p['club_name']}) — {p['assists']} передач")
    else:
        lines.append("  Нет данных")

    lines.append("\n🟨 <b>Самые грубые игроки:</b>")
    if dirty_players:
        for p in dirty_players:
            lines.append(f"  🟨{p['yellow_cards']} 🟥{p['red_cards']} — {p['name']} ({p['club_name']})")
    else:
        lines.append("  Нет данных")

    await message.answer("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /next_season pipeline (called from admin panel too)
# ---------------------------------------------------------------------------

@router.message(Command("next_season"))
async def cmd_next_season(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return
    await _do_next_season(message, message.bot)


async def _do_next_season(message: Message, bot):
    """Full next-season pipeline: awards → budget distribution → rollover."""
    await message.answer("🏆 <b>Начинаю процедуру смены сезона…</b>", parse_mode="HTML")

    async with get_db() as db:
        season = await get_current_season(db)
        standings = await get_standings(db, season)
        clubs = await get_all_clubs(db)

    if not standings:
        await message.answer("❌ Нет данных таблицы. Сыграйте хотя бы один матч.")
        return

    # Step 1: Awards
    await message.answer("🎖 Шаг 1: Запускаю церемонию награждения…")
    champion = standings[0]["name"] if standings else "?"
    champion_id = standings[0]["club_id"] if standings else None
    top_scorer_name = "—"
    top_scorer_goals = 0

    async with get_db() as db:
        for club in clubs:
            async with db.execute(
                "SELECT name, goals FROM players WHERE club_id=? AND is_retired=0 ORDER BY goals DESC LIMIT 1",
                (club["id"],),
            ) as cur:
                row = await cur.fetchone()
            if row and row["goals"] > top_scorer_goals:
                top_scorer_goals = row["goals"]
                top_scorer_name = row["name"]

    season_stats = {
        "season": season,
        "champion": champion,
        "top_scorer_name": top_scorer_name,
        "top_scorer_goals": top_scorer_goals,
    }

    try:
        from ai_service import run_awards_ceremony
        ceremony = await run_awards_ceremony(season_stats)
        awards_msg = (
            f"🏆 <b>Итоги сезона {season}</b>\n"
            f"🥇 Чемпион: <b>{champion}</b>\n"
            f"🌟 Лучший игрок: {ceremony.best_player}\n"
            f"⚽ Бомбардир: {ceremony.top_scorer}\n"
        )
    except Exception as e:
        logger.error(f"Awards failed: {e}")
        awards_msg = f"🏆 Чемпион сезона {season}: <b>{champion}</b>"
        ceremony = None

    await message.answer(awards_msg, parse_mode="HTML")

    # Step 2: Distribute prize money
    await message.answer("💰 Шаг 2: Распределяю призовые средства…")
    prize_lines = []

    async with get_db() as db:
        for pos, row in enumerate(standings, start=1):
            club_id = row["club_id"]
            prize = LEAGUE_PRIZES.get(pos, 800_000)

            # Add prize to budget
            await db.execute(
                "UPDATE clubs SET budget=budget+? WHERE id=?",
                (prize, club_id),
            )
            prize_lines.append(
                f"  #{pos} {row['name']}: +{prize // 1_000_000}M€"
            )

        # Champion trophy bonus
        if champion_id:
            await db.execute(
                "UPDATE clubs SET trophies=trophies+1 WHERE id=?",
                (champion_id,),
            )

        await db.commit()

    prize_text = "💰 <b>Призовые средства распределены:</b>\n" + "\n".join(prize_lines[:10])
    if len(prize_lines) > 10:
        prize_text += f"\n  … и ещё {len(prize_lines) - 10} клубов"
    await message.answer(prize_text, parse_mode="HTML")

    # Step 3: AI calculates next season budgets (optional adjustment)
    await message.answer("🤖 Шаг 3: AI рассчитывает бюджеты следующего сезона…")
    try:
        from ai_service import calculate_next_season_budgets
        async with get_db() as db:
            updated_clubs = await get_all_clubs(db)
        new_budgets = await calculate_next_season_budgets(standings, updated_clubs, ceremony)
        # Apply AI-adjusted budgets
        async with get_db() as db:
            for club_id, new_budget in new_budgets.items():
                await db.execute(
                    "UPDATE clubs SET season_budget=? WHERE id=?",
                    (new_budget, club_id),
                )
            await db.commit()
        await message.answer("✅ Бюджеты следующего сезона рассчитаны.")
    except Exception as e:
        logger.warning(f"Budget calculation failed: {e}")
        await message.answer("⚠️ Не удалось рассчитать бюджеты через AI, используются текущие значения.")

    # Step 4: Season rollover
    await message.answer("🔄 Шаг 4: Запускаю смену сезона…")
    async with get_db() as db:
        await execute_full_season_rollover(db, season)
        new_season = season + 1

    await message.answer(
        f"🎉 <b>Сезон {season} завершён!</b>\n\n"
        f"🏟 <b>Добро пожаловать в сезон {new_season}!</b>\n"
        f"📅 Расписание матчей сгенерировано.\n"
        f"👶 Молодые таланты вошли в составы клубов.\n\n"
        f"Удачного сезона всем менеджерам! ⚽🔥",
        parse_mode="HTML",
    )

    try:
        await bot.send_message(
            GROUP_CHAT_ID,
            f"🚨 <b>Сезон {new_season} начался!</b>\n\n"
            f"🏆 Чемпион прошлого сезона: <b>{champion}</b>\n"
            f"⚽ Лучший бомбардир: <b>{top_scorer_name}</b> ({top_scorer_goals} голов)\n\n"
            f"Новый сезон — новые возможности! Используйте /claim_club для выбора клуба.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Group post failed: {e}")
