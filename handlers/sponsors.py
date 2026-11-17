"""handlers/sponsors.py — Sponsor system handler."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from database import (
    accept_sponsor_offer,
    generate_sponsor_offers,
    get_club_by_owner,
    get_db,
    get_manager,
    get_pending_offers,
    get_sponsor_contract,
)
from keyboards import get_sponsor_accept_keyboard

router = Router()
logger = logging.getLogger(__name__)


def _fmt_budget(amount: int) -> str:
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.1f}M€"
    return f"{amount:,}€"


@router.message(Command("sponsors"))
async def cmd_sponsors(message: Message):
    """Show current sponsor contract and pending offers."""
    try:
        async with get_db() as db:
            manager = await get_manager(db, message.from_user.id)
            if not manager:
                await message.answer("⚽ Сначала выберите клуб: /claim_club")
                return

            club_id = manager["club_id"]
            contract = await get_sponsor_contract(db, club_id)
            offers = await get_pending_offers(db, club_id)

        tier_emoji = {"bronze": "🥉", "silver": "🥈", "gold": "🥇", "platinum": "💎"}

        text = "🏢 <b>Спонсоры вашего клуба</b>\n\n"

        if contract:
            text += (
                f"📝 <b>Текущий контракт:</b>\n"
                f"🏢 Спонсор: <b>{contract['sponsor_name']}</b>\n"
                f"🎯 Цель: попасть в топ-{contract['target_position']}\n"
                f"💰 Бонус при выполнении: <b>{_fmt_budget(contract['bonus_amount'])}</b>\n\n"
            )
        else:
            text += "📋 <i>У вас нет активного спонсорского контракта.</i>\n\n"

        if offers:
            text += f"📬 <b>Предложения спонсоров ({len(offers)}):</b>\n"
            for offer in offers:
                tier = offer.get("tier", "bronze")
                emoji = tier_emoji.get(tier, "📋")
                text += (
                    f"\n{emoji} <b>{offer['sponsor_name']}</b>\n"
                    f"   Уровень: {tier.upper()}\n"
                    f"   Цель: топ-{offer['target_position']}\n"
                    f"   Бонус: {_fmt_budget(offer['bonus_amount'])}\n"
                    f"   Еженедельно: {_fmt_budget(offer['weekly_income'])}\n"
                )
            await message.answer(
                text,
                parse_mode="HTML",
                reply_markup=get_sponsor_accept_keyboard(offers),
            )
        else:
            text += "📭 <i>Нет новых предложений от спонсоров.\nПопросите администратора запустить /offer_sponsors</i>"
            await message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Sponsors error: {e}", exc_info=True)
        await message.answer("❌ Ошибка при загрузке спонсоров. Попробуйте позже.")


@router.callback_query(F.data.startswith("sponsor_accept:"))
async def cb_sponsor_accept(callback: CallbackQuery):
    """Accept a sponsor offer."""
    try:
        offer_id = int(callback.data.split(":")[1])
        user_id = callback.from_user.id

        async with get_db() as db:
            manager = await get_manager(db, user_id)
            if not manager:
                await callback.answer("❌ У вас нет клуба!", show_alert=True)
                return

            club_id = manager["club_id"]
            # Get offer details before accepting
            offers = await get_pending_offers(db, club_id)
            offer = next((o for o in offers if o["id"] == offer_id), None)
            if not offer:
                await callback.answer("❌ Предложение не найдено или уже принято!", show_alert=True)
                return

            success = await accept_sponsor_offer(db, offer_id, club_id)

        if success:
            bonus_m = offer["bonus_amount"] // 1_000_000
            weekly_income = offer["weekly_income"]
            immediate = weekly_income * 10
            await callback.answer(
                f"✅ Контракт с {offer['sponsor_name']} подписан! Получено {_fmt_budget(immediate)} авансом.",
                show_alert=True,
            )
            await callback.message.edit_text(
                f"✅ <b>Контракт подписан!</b>\n\n"
                f"🏢 Спонсор: <b>{offer['sponsor_name']}</b>\n"
                f"🎯 Цель: попасть в топ-{offer['target_position']}\n"
                f"💰 Бонус: <b>{_fmt_budget(offer['bonus_amount'])}</b>\n"
                f"💵 Аванс зачислен: <b>{_fmt_budget(immediate)}</b>",
                parse_mode="HTML",
            )
        else:
            await callback.answer("❌ Не удалось принять предложение.", show_alert=True)

    except Exception as e:
        logger.error(f"Sponsor accept error: {e}", exc_info=True)
        await callback.answer("❌ Произошла ошибка. Попробуйте снова.", show_alert=True)
