"""handlers/transfers.py — Transfer market commands and callbacks."""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import ADMIN_IDS, GROUP_CHAT_ID
from database import (
    get_db,
    get_club,
    get_club_by_owner,
    get_players,
    get_current_season,
    update_club_budget,
    get_game_state,
)
from ai_service import valuate_external_player, generate_transfer_news, PlayerValuation
from keyboards import get_transfer_confirm_keyboard

logger = logging.getLogger(__name__)
router = Router()

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# {player_id: {"from_club_id": int, "to_club_id": int, "fee": float,
#               "seller_confirmed": bool, "buyer_confirmed": bool,
#               "player_name": str, "from_club_name": str, "to_club_name": str}}
pending_transfers: dict[int, dict[str, Any]] = {}

# {f"{owner_id}_{encoded_name}": {"valuation": PlayerValuation, "club_id": int}}
pending_external: dict[str, dict[str, Any]] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FOCUS_EMOJIS = {
    "attack": "⚽ Атака",
    "defense": "🛡️ Оборона",
    "fitness": "💪 Физподготовка",
    "academy": "🌱 Академия",
}


def _fmt_eur(amount: float) -> str:
    """Format euros as compact M€ string."""
    return f"{amount / 1_000_000:.1f}M€"


def _encode_name(name: str) -> str:
    return name.replace(" ", "_")


def _decode_name(encoded: str) -> str:
    return encoded.replace("_", " ")


async def _get_player_by_id(db, player_id: int) -> dict | None:
    async with db.execute("SELECT * FROM players WHERE id = ?", (player_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def _execute_transfer(
    db,
    bot: Bot,
    player_id: int,
    from_club_id: int,
    to_club_id: int,
    fee: float,
    player_name: str,
    from_club_name: str,
    to_club_name: str,
    season: int,
    transfer_type: str = "internal",
) -> None:
    """Apply the transfer in the database and post news."""
    # Move player
    await db.execute(
        "UPDATE players SET club_id = ? WHERE id = ?",
        (to_club_id, player_id),
    )
    # Budget adjustments
    await update_club_budget(db, from_club_id, +fee)
    await update_club_budget(db, to_club_id, -fee)

    # Record in transfers table
    news_obj = await generate_transfer_news(player_name, from_club_name, to_club_name, fee)
    await db.execute(
        """INSERT INTO transfers (season, from_club_id, to_club_id, player_id, fee, transfer_type, news_post)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (season, from_club_id, to_club_id, player_id, fee, transfer_type, news_obj.body),
    )
    await db.commit()

    # Post news to group
    try:
        await bot.send_message(GROUP_CHAT_ID, f"🚨 {news_obj.headline}\n\n{news_obj.body}")
    except Exception:
        logger.exception("Failed to post transfer news to group")


# ---------------------------------------------------------------------------
# /transfer_internal
# ---------------------------------------------------------------------------

@router.message(Command("transfer_internal"))
async def cmd_transfer_internal(message: Message, bot: Bot) -> None:
    """
    Usage: /transfer_internal <player_id> <to_club_id> <fee_millions>
    Example: /transfer_internal 5 3 15  →  player #5 to club #3 for 15M€
    """
    args = (message.text or "").split()
    if len(args) != 4:
        await message.reply(
            "❌ Использование: /transfer_internal <id_игрока> <id_клуба_покупателя> <сумма_млн>\n"
            "Пример: /transfer_internal 5 3 15"
        )
        return

    try:
        player_id = int(args[1])
        to_club_id = int(args[2])
        fee = float(args[3]) * 1_000_000
    except ValueError:
        await message.reply("❌ Неверный формат аргументов. Используйте числа.")
        return

    caller_id = message.from_user.id

    async with await get_db() as db:
        is_open = await get_game_state(db, "transfer_window_open", "1")
        if is_open == "0":
            await message.reply("🔒 Трансферное окно сейчас закрыто! Ожидайте открытия администратором.")
            return

        # Verify caller owns a club
        seller_club = await get_club_by_owner(db, caller_id)
        if seller_club is None:
            await message.reply("❌ У вас нет клуба.")
            return

        # Verify player belongs to seller's club
        player = await _get_player_by_id(db, player_id)
        if player is None or player["club_id"] != seller_club["id"]:
            await message.reply("❌ Этот игрок не принадлежит вашему клубу.")
            return

        if player.get("is_retired"):
            await message.reply("❌ Нельзя продать завершившего карьеру игрока.")
            return

        # Verify buying club exists
        buyer_club = await get_club(db, to_club_id)
        if buyer_club is None:
            await message.reply(f"❌ Клуб #{to_club_id} не найден.")
            return

        if buyer_club["id"] == seller_club["id"]:
            await message.reply("❌ Нельзя продать игрока самому себе.")
            return

        # Verify buyer has budget
        if buyer_club["budget"] < fee:
            await message.reply(
                f"❌ У клуба «{buyer_club['name']}» недостаточно средств.\n"
                f"Нужно: {_fmt_eur(fee)} | В наличии: {_fmt_eur(buyer_club['budget'])}"
            )
            return

        season = await get_current_season(db)
        from_club_id = seller_club["id"]
        player_name = player["name"]
        from_club_name = seller_club["name"]
        to_club_name = buyer_club["name"]

        # Store pending transfer (seller already confirmed by initiating)
        pending_transfers[player_id] = {
            "from_club_id": from_club_id,
            "to_club_id": to_club_id,
            "fee": fee,
            "seller_confirmed": True,
            "buyer_confirmed": False,
            "player_name": player_name,
            "from_club_name": from_club_name,
            "to_club_name": to_club_name,
            "season": season,
        }

        buyer_owner_id: int | None = buyer_club.get("owner_id")

        if buyer_owner_id:
            # Human buyer — send confirmation request
            keyboard = get_transfer_confirm_keyboard(player_id, from_club_id, to_club_id, int(fee))
            try:
                await bot.send_message(
                    GROUP_CHAT_ID,
                    f"🤝 *Предложение о трансфере*\n\n"
                    f"🏟️ «{from_club_name}» предлагает игрока *{player_name}*\n"
                    f"➡️ Клуб: «{to_club_name}»\n"
                    f"💰 Сумма: *{_fmt_eur(fee)}*\n\n"
                    f"👤 Владелец «{to_club_name}» (ID: {buyer_owner_id}), подтвердите или отклоните сделку:",
                    reply_markup=keyboard,
                    parse_mode="Markdown",
                )
            except Exception:
                logger.exception("Failed to send transfer offer to group")

            await message.reply(
                f"📝 Предложение отправлено клубу «{to_club_name}». Ожидаем подтверждения..."
            )
        else:
            # AI buyer — auto-confirm and execute immediately
            await _execute_transfer(
                db, bot, player_id, from_club_id, to_club_id, fee,
                player_name, from_club_name, to_club_name, season,
            )
            del pending_transfers[player_id]
            await message.reply(
                f"✅ Трансфер завершён!\n"
                f"⚽ *{player_name}* перешёл в «{to_club_name}» за *{_fmt_eur(fee)}*",
                parse_mode="Markdown",
            )


# ---------------------------------------------------------------------------
# /buy_external
# ---------------------------------------------------------------------------

@router.message(Command("buy_external"))
async def cmd_buy_external(message: Message) -> None:
    """
    Usage: /buy_external <Имя Игрока>
    Example: /buy_external Kylian Mbappe
    """
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "❌ Использование: /buy_external <Имя игрока>\n"
            "Пример: /buy_external Kylian Mbappe"
        )
        return

    player_name = parts[1].strip()
    caller_id = message.from_user.id

    async with await get_db() as db:
        is_open = await get_game_state(db, "transfer_window_open", "1")
        if is_open == "0":
            await message.reply("🔒 Трансферное окно сейчас закрыто! Ожидайте открытия администратором.")
            return

        club = await get_club_by_owner(db, caller_id)
        if club is None:
            await message.reply("❌ У вас нет клуба. Сначала зарегистрируйте команду.")
            return

        season = await get_current_season(db)

    await message.reply(f"🔍 Оцениваю стоимость игрока *{player_name}*...", parse_mode="Markdown")

    try:
        valuation: PlayerValuation = await valuate_external_player(player_name)
    except Exception as exc:
        logger.exception("valuate_external_player failed")
        await message.reply(f"❌ Не удалось получить оценку игрока: {exc}")
        return

    encoded = _encode_name(player_name)
    key = f"{caller_id}_{encoded}"
    pending_external[key] = {"valuation": valuation, "club_id": club["id"], "season": season}

    price_m = valuation.markup_value_eur / 1_000_000
    market_m = valuation.market_value_eur / 1_000_000

    # Build confirm keyboard
    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"✅ Купить за {price_m:.1f}M€",
        callback_data=f"transfer_confirm_external:{caller_id}:{encoded}:{int(valuation.markup_value_eur)}",
    )
    builder.button(text="❌ Отмена", callback_data=f"transfer_cancel_external:{caller_id}:{encoded}")
    builder.adjust(1)

    text = (
        f"🌟 *Оценка игрока: {valuation.player_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Позиция: *{valuation.position}*\n"
        f"🎂 Возраст: *{valuation.age}* лет\n"
        f"⭐ Рейтинг OVR: *{valuation.ovr}*\n"
        f"💶 Рыночная стоимость: *{market_m:.1f}M€*\n"
        f"🏷️ Цена покупки (с наценкой): *{price_m:.1f}M€*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 *Анализ:*\n{valuation.valuation_reasoning}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Ваш бюджет: *{_fmt_eur(club['budget'])}*"
    )

    await message.reply(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /transfer_rumor
# ---------------------------------------------------------------------------

@router.message(Command("transfer_rumor"))
async def cmd_transfer_rumor(message: Message, bot: Bot) -> None:
    """
    Usage: /transfer_rumor <player_id> <to_club_id>
    """
    args = (message.text or "").split()
    if len(args) != 3:
        await message.reply(
            "❌ Использование: /transfer_rumor <id_игрока> <id_клуба>\n"
            "Пример: /transfer_rumor 12 5"
        )
        return

    try:
        player_id = int(args[1])
        to_club_id = int(args[2])
    except ValueError:
        await message.reply("❌ Неверный формат аргументов.")
        return

    async with await get_db() as db:
        player = await _get_player_by_id(db, player_id)
        if player is None:
            await message.reply(f"❌ Игрок #{player_id} не найден.")
            return

        from_club = await get_club(db, player["club_id"])
        to_club = await get_club(db, to_club_id)
        if to_club is None:
            await message.reply(f"❌ Клуб #{to_club_id} не найден.")
            return

    from_club_name = from_club["name"] if from_club else "Неизвестный клуб"
    to_club_name = to_club["name"]

    try:
        news = await generate_transfer_news(
            player["name"], from_club_name, to_club_name, fee=0.0, is_rumor=True
        )
    except Exception as exc:
        logger.exception("generate_transfer_news (rumor) failed")
        await message.reply(f"❌ Ошибка генерации слуха: {exc}")
        return

    try:
        await bot.send_message(GROUP_CHAT_ID, f"👀 {news.headline}\n\n{news.body}")
        await message.reply("✅ Слух опубликован в группе!")
    except Exception:
        logger.exception("Failed to post rumor to group")
        await message.reply(f"👀 {news.headline}\n\n{news.body}")


# ---------------------------------------------------------------------------
# /list_transfers
# ---------------------------------------------------------------------------

@router.message(Command("list_transfers"))
async def cmd_list_transfers(message: Message) -> None:
    """Show the last 10 transfers for the current season."""
    async with await get_db() as db:
        season = await get_current_season(db)
        async with db.execute(
            """SELECT t.id, t.fee, t.transfer_type, t.timestamp,
                      p.name AS player_name,
                      c1.name AS from_club, c2.name AS to_club
               FROM transfers t
               JOIN players p  ON p.id  = t.player_id
               LEFT JOIN clubs c1 ON c1.id = t.from_club_id
               JOIN clubs c2 ON c2.id = t.to_club_id
               WHERE t.season = ?
               ORDER BY t.id DESC
               LIMIT 10""",
            (season,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await message.reply(f"📋 В сезоне {season} трансферов ещё не было.")
        return

    lines = [f"🔄 *Трансферы сезона {season}* (последние 10)\n━━━━━━━━━━━━━━━━━━━━━"]
    for i, row in enumerate(rows, 1):
        t_type = "🌍" if row["transfer_type"] == "external" else "🔄"
        from_name = row["from_club"] or "Свободный агент"
        fee_str = _fmt_eur(row["fee"]) if row["fee"] else "б/у"
        lines.append(
            f"{i}. {t_type} *{row['player_name']}*\n"
            f"   {from_name} ➡️ {row['to_club']} | 💰 {fee_str}"
        )

    await message.reply("\n".join(lines), parse_mode="Markdown")


# ---------------------------------------------------------------------------
# /player_value
# ---------------------------------------------------------------------------

@router.message(Command("player_value"))
async def cmd_player_value(message: Message) -> None:
    """
    Usage: /player_value <player_id>  OR  /player_value <Имя Игрока>
    """
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "❌ Использование: /player_value <id_игрока> или /player_value <Имя игрока>\n"
            "Пример: /player_value 7  или  /player_value Lamine Yamal"
        )
        return

    query = parts[1].strip()

    # Try to parse as int (player_id)
    player_from_db: dict | None = None
    try:
        player_id = int(query)
        async with await get_db() as db:
            player_from_db = await _get_player_by_id(db, player_id)
            if player_from_db:
                club = await get_club(db, player_from_db["club_id"])
                player_from_db["_club_name"] = club["name"] if club else "Неизвестный"
    except ValueError:
        pass

    if player_from_db:
        # Calculate value from DB stats
        ovr = player_from_db["ovr"]
        age = player_from_db["age"]
        position = player_from_db["position"]

        # Age factor
        if 25 <= age <= 27:
            age_factor = 1.2
        elif age < 21:
            age_factor = 0.8
        elif age > 30:
            age_factor = 0.7
        else:
            age_factor = 1.0

        # Position factor
        position_factors = {
            "ST": 1.15, "CF": 1.15,
            "LW": 1.10, "RW": 1.10,
            "CAM": 1.05,
            "CM": 1.0, "CDM": 0.95,
            "LB": 0.90, "RB": 0.90,
            "CB": 0.85,
            "GK": 0.80,
        }
        pos_factor = position_factors.get(position, 1.0)

        base_value = (ovr ** 2) * 10_000
        estimated_value = base_value * age_factor * pos_factor

        potential_note = ""
        if age < 21:
            potential_note = "\n🌱 *Молодой игрок:* высокий потенциал роста!"

        await message.reply(
            f"💎 *Оценка игрока из базы данных*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚽ *{player_from_db['name']}* ({player_from_db['_club_name']})\n"
            f"📍 Позиция: {position}\n"
            f"🎂 Возраст: {age} лет\n"
            f"⭐ OVR: {ovr}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 Расчёт стоимости:\n"
            f"   • База (OVR²×10k): {_fmt_eur(ovr ** 2 * 10_000)}\n"
            f"   • Возрастной коэф.: ×{age_factor}\n"
            f"   • Позиционный коэф.: ×{pos_factor:.2f}\n"
            f"💰 *Оценочная стоимость: {_fmt_eur(estimated_value)}*{potential_note}",
            parse_mode="Markdown",
        )
        return

    # Not in DB or name given — use AI valuation
    await message.reply(f"🔍 Запрашиваю оценку *{query}* у экспертов...", parse_mode="Markdown")

    try:
        valuation = await valuate_external_player(query)
    except Exception as exc:
        logger.exception("valuate_external_player failed for /player_value")
        await message.reply(f"❌ Не удалось получить оценку: {exc}")
        return

    market_m = valuation.market_value_eur / 1_000_000
    markup_m = valuation.markup_value_eur / 1_000_000

    await message.reply(
        f"🌟 *Оценка игрока: {valuation.player_name}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 Позиция: {valuation.position}\n"
        f"🎂 Возраст: {valuation.age} лет\n"
        f"⭐ OVR: {valuation.ovr}\n"
        f"💶 Рыночная стоимость: *{market_m:.1f}M€*\n"
        f"🏷️ Цена покупки: *{markup_m:.1f}M€*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📝 {valuation.valuation_reasoning}",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback: transfer_confirm:{player_id}:{from_id}:{to_id}:{fee}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("transfer_confirm:"))
async def cb_transfer_confirm(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 5:
        await callback.answer("❌ Неверные данные.", show_alert=True)
        return

    _, player_id_str, from_id_str, to_id_str, fee_str = parts
    player_id = int(player_id_str)
    from_club_id = int(from_id_str)
    to_club_id = int(to_id_str)
    fee = float(fee_str)

    caller_id = callback.from_user.id

    async with await get_db() as db:
        caller_club = await get_club_by_owner(db, caller_id)

    if caller_club is None:
        await callback.answer("❌ У вас нет клуба.", show_alert=True)
        return

    pending = pending_transfers.get(player_id)
    if pending is None:
        await callback.answer(
            "⚠️ Это предложение уже обработано или истекло.", show_alert=True
        )
        return

    is_seller = caller_club["id"] == from_club_id
    is_buyer = caller_club["id"] == to_club_id

    if not is_seller and not is_buyer:
        await callback.answer("❌ Это не ваш трансфер.", show_alert=True)
        return

    if is_seller:
        pending["seller_confirmed"] = True
    if is_buyer:
        pending["buyer_confirmed"] = True

    if pending["seller_confirmed"] and pending["buyer_confirmed"]:
        # Both confirmed — execute
        async with await get_db() as db:
            # Final budget re-check
            buyer_club = await get_club(db, to_club_id)
            if buyer_club and buyer_club["budget"] < fee:
                del pending_transfers[player_id]
                await callback.answer(
                    "❌ Недостаточно средств у покупателя — трансфер отменён.", show_alert=True
                )
                return

            await _execute_transfer(
                db, bot, player_id,
                pending["from_club_id"], pending["to_club_id"], fee,
                pending["player_name"], pending["from_club_name"], pending["to_club_name"],
                pending["season"],
            )
        del pending_transfers[player_id]
        await callback.answer("✅ Трансфер завершён!", show_alert=True)
        try:
            await callback.message.edit_text(
                f"✅ *Трансфер завершён!*\n"
                f"⚽ *{pending['player_name']}* перешёл в «{pending['to_club_name']}»\n"
                f"💰 Сумма: *{_fmt_eur(fee)}*",
                parse_mode="Markdown",
            )
        except Exception:
            pass
    else:
        await callback.answer(
            "✅ Ваше подтверждение принято. Ждём второй стороны.", show_alert=True
        )


# ---------------------------------------------------------------------------
# Callback: transfer_cancel:{player_id}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("transfer_cancel:"))
async def cb_transfer_cancel(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 2:
        await callback.answer("❌ Неверные данные.", show_alert=True)
        return

    player_id = int(parts[1])
    pending = pending_transfers.pop(player_id, None)

    if pending is None:
        await callback.answer("⚠️ Трансфер уже был обработан.", show_alert=True)
        return

    await callback.answer("❌ Трансфер отменён", show_alert=True)
    try:
        await callback.message.edit_text(
            f"❌ Трансфер игрока *{pending['player_name']}* отменён.",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Callback: transfer_confirm_external:{owner_id}:{encoded_player_name}:{fee}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("transfer_confirm_external:"))
async def cb_transfer_confirm_external(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("❌ Неверные данные.", show_alert=True)
        return

    _, owner_id_str, encoded_name, fee_str = parts
    owner_id = int(owner_id_str)
    fee = float(fee_str)
    player_name = _decode_name(encoded_name)
    caller_id = callback.from_user.id

    if caller_id != owner_id:
        await callback.answer("❌ Это не ваша операция.", show_alert=True)
        return

    key = f"{owner_id}_{encoded_name}"
    pending = pending_external.get(key)
    if pending is None:
        await callback.answer("⚠️ Запрос устарел или уже выполнен.", show_alert=True)
        return

    valuation: PlayerValuation = pending["valuation"]
    club_id: int = pending["club_id"]
    season: int = pending.get("season", 1)

    async with await get_db() as db:
        club = await get_club(db, club_id)
        if club is None:
            await callback.answer("❌ Клуб не найден.", show_alert=True)
            return

        if club["budget"] < fee:
            await callback.answer(
                f"❌ Недостаточно средств! "
                f"Нужно {_fmt_eur(fee)}, есть {_fmt_eur(club['budget'])}.",
                show_alert=True,
            )
            return

        # Deduct fee
        await update_club_budget(db, club_id, -fee)

        # Add player to DB
        await db.execute(
            """INSERT INTO players (club_id, name, position, age, ovr, season_joined)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (club_id, valuation.player_name, valuation.position,
             valuation.age, valuation.ovr, season),
        )
        await db.commit()

        # Get the new player id
        async with db.execute(
            "SELECT id FROM players WHERE club_id = ? AND name = ? ORDER BY id DESC LIMIT 1",
            (club_id, valuation.player_name),
        ) as cur:
            new_row = await cur.fetchone()
        new_player_id = new_row["id"] if new_row else 0

        # Record transfer
        news_obj = await generate_transfer_news(
            valuation.player_name, "Свободный агент", club["name"], fee
        )
        await db.execute(
            """INSERT INTO transfers (season, from_club_id, to_club_id, player_id, fee, transfer_type, news_post)
               VALUES (?, NULL, ?, ?, ?, 'external', ?)""",
            (season, club_id, new_player_id, fee, news_obj.body),
        )
        await db.commit()

    del pending_external[key]

    try:
        await bot.send_message(GROUP_CHAT_ID, f"🚨 {news_obj.headline}\n\n{news_obj.body}")
    except Exception:
        logger.exception("Failed to post external transfer news")

    await callback.answer("✅ Игрок куплен!", show_alert=True)
    try:
        await callback.message.edit_text(
            f"✅ *{valuation.player_name}* успешно куплен!\n"
            f"📍 Позиция: {valuation.position} | ⭐ OVR: {valuation.ovr}\n"
            f"💰 Потрачено: *{_fmt_eur(fee)}*",
            parse_mode="Markdown",
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Callback: transfer_cancel_external:{owner_id}:{encoded_name}
# ---------------------------------------------------------------------------

@router.callback_query(F.data.startswith("transfer_cancel_external:"))
async def cb_transfer_cancel_external(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer("❌ Неверные данные.", show_alert=True)
        return

    _, owner_id_str, encoded_name = parts
    owner_id = int(owner_id_str)
    caller_id = callback.from_user.id

    if caller_id != owner_id:
        await callback.answer("❌ Это не ваша операция.", show_alert=True)
        return

    key = f"{owner_id}_{encoded_name}"
    pending_external.pop(key, None)

    player_name = _decode_name(encoded_name)
    await callback.answer("❌ Покупка отменена", show_alert=True)
    try:
        await callback.message.edit_text(
            f"❌ Покупка игрока *{player_name}* отменена.",
            parse_mode="Markdown",
        )
    except Exception:
        pass
