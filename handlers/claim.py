"""
handlers/claim.py — Club claim and automatic manager registration.
Allows users to choose and claim a club via /claim_club with inline buttons,
and immediately registers them as a manager in the database.
"""

from __future__ import annotations

import logging
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from config import GROUP_CHAT_ID
from database import (
    get_db,
    get_club,
    get_club_by_owner,
    get_available_clubs,
    create_manager,
    get_manager,
)
from keyboards import get_claim_club_keyboard, CB_CLAIM

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_budget(amount: float) -> str:
    m = amount / 1_000_000
    if m == int(m):
        return f"{int(m)}M€"
    return f"{m:.1f}M€"


# ---------------------------------------------------------------------------
# /claim_club Command
# ---------------------------------------------------------------------------

@router.message(Command("claim_club", "claim"))
async def cmd_claim_club(message: Message) -> None:
    """
    Shows a list of available La Liga clubs for a user to claim.
    If the user already has a club, notifies them.
    """
    user_id = message.from_user.id

    async with await get_db() as db:
        existing_club = await get_club_by_owner(db, user_id)
        if existing_club:
            await message.reply(
                f"❌ <b>Вы уже являетесь менеджером клуба «{existing_club['name']}»!</b>\n\n"
                f"Один пользователь может управлять только одной командой.\n"
                f"Используйте команду /myclub для просмотра состава и финансов своего клуба.",
                parse_mode="HTML",
            )
            return

        available_clubs = await get_available_clubs(db)

    if not available_clubs:
        await message.reply(
            "⚠️ <b>Все клубы Ла Лиги уже заняты менеджерами!</b>\n"
            "Обратитесь к администратору лиги для участия.",
            parse_mode="HTML",
        )
        return

    keyboard = get_claim_club_keyboard(available_clubs)
    text = (
        "📋 <b>ВЫБОР КЛУБА ЛА ЛИГИ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите свободный клуб, который вы хотите возглавить.\n"
        "Вы сразу будете зарегистрированы как главный тренер и менеджер команды!\n\n"
        "<i>Обозначения:</i>\n"
        "🥇 — Гранды (высокие цели и бюджет)\n"
        "🥈 — Крепкие середняки\n"
        "🥉 — Андердоги (высокие спонсорские бонусы за прогресс)\n\n"
        "<i>Нажмите на кнопку с названием клуба ниже:</i>"
    )
    await message.reply(text, parse_mode="HTML", reply_markup=keyboard)


# ---------------------------------------------------------------------------
# Callback: claim_club:{club_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("claim_club:") | F.data.startswith("claim:"))
async def cb_claim_club(callback: CallbackQuery) -> None:
    """
    Handles inline button click to select a club:
    1. Validates that the club is free and user does not already have a club.
    2. Records the user as owner in the clubs table.
    3. Automatically registers the user as a manager in the database.
    """
    parts = callback.data.split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        await callback.answer("❌ Неверные данные запроса.", show_alert=True)
        return

    club_id = int(parts[1])
    user = callback.from_user

    async with await get_db() as db:
        # 1. Verify user doesn't already manage a club
        user_club = await get_club_by_owner(db, user.id)
        if user_club:
            await callback.answer(
                f"❌ Вы уже управляете клубом «{user_club['name']}»!",
                show_alert=True,
            )
            return

        # 2. Check club availability
        club = await get_club(db, club_id)
        if not club:
            await callback.answer("❌ Клуб не найден в базе данных.", show_alert=True)
            return

        if club["owner_id"] is not None and club["owner_id"] != user.id:
            await callback.answer(
                f"❌ Клуб «{club['name']}» уже занят другим менеджером!",
                show_alert=True,
            )
            return

        # 3. AUTOMATIC MANAGER REGISTRATION & CLUB ASSIGNMENT
        # Writes owner_id to clubs table AND registers manager in managers table
        await create_manager(
            db=db,
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            club_id=club_id,
        )

        logger.info(
            "User %s (%d) registered as manager of %s (ID %d)",
            user.full_name,
            user.id,
            club["name"],
            club_id,
        )

    # 4. Acknowledge callback
    await callback.answer("✅ Клуб успешно выбран!", show_alert=False)

    # 5. Success message to the new manager
    budget_str = _fmt_budget(club["budget"])
    confirmation_text = (
        f"🎉 <b>ПОЗДРАВЛЯЕМ! ВЫ НАЗНАЧЕНЫ ГЛАВНЫМ ТРЕНЕРОМ!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏟️ Клуб: <b>{club['name']}</b>\n"
        f"💰 Трансферный бюджет: <b>{budget_str}</b>\n"
        f"👤 Менеджер: <b>{user.full_name}</b>\n\n"
        f"✅ <i>Вы успешно зарегистрированы в базе данных как официальный менеджер. "
        f"Все клубные команды теперь активны!</i>\n\n"
        f"<b>📋 С чего начать:</b>\n"
        f"  /myclub — Состав команды, тактика и бюджет\n"
        f"  /train — Провести недельную тренировку и прокачать игроков\n"
        f"  /prematch — Выбрать схему и капитана перед матчем\n"
        f"  /standings — Турнирная таблица Ла Лиги\n"
        f"  /buy_external — Оценить и купить звезду на трансферном рынке\n"
    )

    try:
        await callback.message.edit_text(confirmation_text, parse_mode="HTML")
    except Exception:
        await callback.message.answer(confirmation_text, parse_mode="HTML")

    # 6. Announce in group chat
    if GROUP_CHAT_ID:
        username_mention = f"@{user.username}" if user.username else user.full_name
        announce_text = (
            f"🚨 <b>ОФИЦИАЛЬНОЕ НАЗНАЧЕНИЕ В ЛА ЛИГЕ!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👔 <b>{user.full_name}</b> ({username_mention}) официально возглавил клуб <b>«{club['name']}»</b>!\n\n"
            f"💰 Стартовый бюджет клуба: <b>{budget_str}</b>\n"
            f"🇪🇸 Пожелаем новому наставнику побед и ярких матчей!"
        )
        try:
            await callback.bot.send_message(GROUP_CHAT_ID, announce_text, parse_mode="HTML")
        except Exception as exc:
            logger.warning("Could not send claim announcement to group chat: %s", exc)
