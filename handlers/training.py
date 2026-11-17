"""handlers/training.py — Training session handler."""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import (
    get_club_by_owner,
    get_current_round,
    get_current_season,
    get_db,
    get_manager,
    get_players,
)

router = Router()
logger = logging.getLogger(__name__)

VALID_FOCUSES = ["Атака", "Оборона", "Физика", "Академия"]


@router.message(Command("train"))
async def cmd_train(message: Message):
    """
    /train <focus>
    Focus options: Атака, Оборона, Физика, Академия
    """
    args = message.text.split(maxsplit=1)
    focus = args[1].strip().capitalize() if len(args) > 1 else ""

    if not focus or focus not in VALID_FOCUSES:
        opts = ", ".join(VALID_FOCUSES)
        await message.answer(
            f"ℹ️ Использование: <code>/train Фокус</code>\n\n"
            f"Доступные фокусы: {opts}\n\n"
            f"Пример: <code>/train Атака</code>",
            parse_mode="HTML",
        )
        return

    async with get_db() as db:
        manager = await get_manager(db, message.from_user.id)
        if not manager:
            await message.answer("⚽ Сначала выберите клуб: /claim_club")
            return

        club = await get_club_by_owner(db, message.from_user.id)
        if not club:
            await message.answer("❌ Клуб не найден.")
            return

        players = await get_players(db, club["id"])
        season = await get_current_season(db)
        rnd = await get_current_round(db)

        # Check if already trained this round
        async with db.execute(
            "SELECT id FROM training_log WHERE club_id=? AND season=? AND round_num=?",
            (club["id"], season, rnd),
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            await message.answer(
                f"⏳ Вы уже провели тренировку в туре {rnd}.\n"
                f"Следующая тренировка доступна после следующего тура."
            )
            return

    if not players:
        await message.answer("❌ В вашем клубе нет игроков.")
        return

    await message.answer(
        f"🏋️ <b>Тренировка {club['name']}</b>\n"
        f"📌 Фокус: <b>{focus}</b>\n\n"
        f"⏳ Анализирую состав и готовлю отчёт…",
        parse_mode="HTML",
    )

    # Filter players by focus
    if focus == "Атака":
        relevant_players = [p for p in players if p["position"] in ("ST", "LW", "RW", "CAM")]
    elif focus == "Оборона":
        relevant_players = [p for p in players if p["position"] in ("GK", "CB", "LB", "RB", "CDM")]
    elif focus == "Академия":
        relevant_players = [p for p in players if p.get("age", 25) <= 22]
    else:  # Физика - все
        relevant_players = players

    if not relevant_players:
        relevant_players = players[:5]

    # Call AI for training report
    try:
        from ai_service import generate_training_report
        report = await generate_training_report(club, focus, relevant_players)
    except Exception as e:
        logger.error(f"Training report error: {e}")
        await message.answer("❌ Не удалось сгенерировать отчёт тренировки. Попробуйте позже.")
        return

    # Apply improvements to DB
    improvements_text = []
    async with get_db() as db:
        for imp in report.improvements:
            player_name = imp.player_name
            ovr_gain = imp.ovr_gain
            injury_risk = imp.injury_risk

            if ovr_gain > 0:
                await db.execute(
                    "UPDATE players SET ovr=MIN(99, ovr+?) WHERE club_id=? AND LOWER(name) LIKE ? AND is_retired=0",
                    (ovr_gain, club["id"], f"%{player_name.lower()}%"),
                )
                improvements_text.append(f"  📈 {player_name}: +{ovr_gain} OVR")

            if injury_risk:
                await db.execute(
                    "UPDATE players SET injury_rounds=injury_rounds+1 WHERE club_id=? AND LOWER(name) LIKE ? AND is_retired=0",
                    (club["id"], f"%{player_name.lower()}%"),
                )
                improvements_text.append(f"  🤕 {player_name}: травма на тренировке!")

        # Log the training session
        await db.execute(
            "INSERT INTO training_log (club_id, season, round_num, focus, report) VALUES (?,?,?,?,?)",
            (club["id"], season, rnd, focus, report.summary),
        )
        await db.commit()

    # Format report
    impr_section = "\n".join(improvements_text) if improvements_text else "  Нет значительных улучшений"

    text = (
        f"🏋️ <b>Отчёт о тренировке — {club['name']}</b>\n"
        f"📌 Фокус: <b>{focus}</b> | Тур {rnd}, Сезон {season}\n"
        f"{'─' * 32}\n\n"
        f"📄 <b>Описание:</b>\n{report.summary}\n\n"
        f"🌟 <b>Лучший момент:</b>\n{report.highlight}\n\n"
        f"📈 <b>Улучшения игроков:</b>\n{impr_section}"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("training_history"))
async def cmd_training_history(message: Message):
    """Show recent training sessions for the user's club."""
    async with get_db() as db:
        manager = await get_manager(db, message.from_user.id)
        if not manager:
            await message.answer("⚽ Сначала выберите клуб: /claim_club")
            return

        from database import get_club
        club = await get_club(db, manager["club_id"])
        if not club:
            await message.answer("❌ Клуб не найден.")
            return

        async with db.execute(
            """SELECT * FROM training_log WHERE club_id=? ORDER BY created_at DESC LIMIT 10""",
            (club["id"],),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await message.answer(f"📋 История тренировок {club['name']} пуста.")
        return

    lines = [f"📋 <b>История тренировок — {club['name']}</b>\n"]
    for row in rows:
        focus_emoji = {
            "Атака": "⚔️", "Оборона": "🛡", "Физика": "💪", "Академия": "🎓"
        }.get(row["focus"], "🏋️")
        lines.append(
            f"{focus_emoji} <b>{row['focus']}</b> | Сезон {row['season']}, Тур {row['round_num']}\n"
            f"   <i>{row['report'][:100]}…</i>"
        )

    await message.answer("\n\n".join(lines), parse_mode="HTML")
