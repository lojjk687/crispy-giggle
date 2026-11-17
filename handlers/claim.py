"""handlers/claim.py — Club claiming handler."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import GROUP_CHAT_ID
from database import (
    create_manager,
    get_available_clubs,
    get_club,
    get_db,
    get_manager,
)
from keyboards import get_claim_club_keyboard

router = Router()
logger = logging.getLogger(__name__)


@router.message(Command("claim_club"))
async def cmd_claim_club(message: Message):
    """Show available clubs to claim."""
    async with get_db() as db:
        manager = await get_manager(db, message.from_user.id)
        if manager:
            club = await get_club(db, manager["club_id"])
            cname = club["name"] if club else "неизвестный клуб"
            await message.answer(
                f"⚽ Вы уже управляете клубом <b>{cname}</b>!\n"
                "Используйте /myclub для просмотра информации.",
                parse_mode="HTML",
            )
            return

        clubs = await get_available_clubs(db)

    if not clubs:
        await message.answer(
            "😔 К сожалению, все клубы уже заняты.\n"
            "Следите за обновлениями — возможно, места освободятся!"
        )
        return

    keyboard = get_claim_club_keyboard(clubs)
    await message.answer(
        "🏟 <b>Доступные клубы La Liga</b>\n\n"
        "Выберите клуб, которым хотите управлять:",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("claim:"))
async def cb_claim_club(callback: CallbackQuery):
    """Process club selection callback."""
    try:
        club_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("❌ Неверный запрос.", show_alert=True)
        return

    user_id = callback.from_user.id
    username = callback.from_user.username or callback.from_user.full_name

    async with get_db() as db:
        # Check if user already has a club
        existing = await get_manager(db, user_id)
        if existing:
            ec = await get_club(db, existing["club_id"])
            await callback.answer(
                f"❌ Вы уже управляете {ec['name'] if ec else 'клубом'}!",
                show_alert=True,
            )
            return

        # Check if club is still available
        club = await get_club(db, club_id)
        if not club:
            await callback.answer("❌ Клуб не найден.", show_alert=True)
            return
        if club.get("owner_id"):
            await callback.answer(
                "❌ Этот клуб уже занят! Выберите другой.",
                show_alert=True,
            )
            return

        # Assign club
        manager = await create_manager(db, user_id, club_id, username)

    budget_m = club["budget"] // 1_000_000
    tier_names = {1: "Топ-клуб ⭐⭐⭐", 2: "Середняк ⭐⭐", 3: "Малый клуб ⭐"}
    tier_label = tier_names.get(club.get("tier", 3), "Клуб")

    # Edit the original message
    await callback.message.edit_text(
        f"✅ <b>Вы стали тренером {club['name']}!</b>\n\n"
        f"🏟 Стадион: {club['name']}\n"
        f"💰 Бюджет: {budget_m}M€\n"
        f"📊 Уровень: {tier_label}\n\n"
        f"Удачи в сезоне, {username}! 🎉",
        parse_mode="HTML",
    )
    await callback.answer()

    # Group announcement
    try:
        from aiogram import Bot
        # We'll use the bot instance from the callback
        bot = callback.bot
        await bot.send_message(
            GROUP_CHAT_ID,
            f"🚨 <b>Новый менеджер в La Liga!</b>\n\n"
            f"@{username} принял руководство клубом <b>{club['name']}</b>!\n"
            f"💰 Стартовый бюджет: {budget_m}M€\n\n"
            f"Добро пожаловать в La Liga Dynasty! ⚽🔥",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Could not send group announcement: {e}")
