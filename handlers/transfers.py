"""handlers/transfers.py — Internal and external transfer handling."""

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from config import GROUP_CHAT_ID
from database import (
    get_all_clubs,
    get_club,
    get_club_by_owner,
    get_db,
    get_game_state,
    get_manager,
    get_player_by_name,
    update_club_budget,
    get_free_agents,
    get_free_agent_by_name,
    delete_free_agent,
)

router = Router()
logger = logging.getLogger(__name__)


def _fmt_budget(amount: int) -> str:
    return f"{amount / 1_000_000:.1f}M€"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _post_to_group(bot, text: str):
    """Safe group announcement."""
    try:
        await bot.send_message(GROUP_CHAT_ID, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Group post failed: {e}")


async def _record_transfer(db, season: int, from_club_id, to_club_id: int,
                           player_id: int, fee: int, transfer_type: str):
    """Log transfer into the transfers table."""
    await db.execute(
        """INSERT INTO transfers (season, from_club_id, to_club_id, player_id, fee, transfer_type)
           VALUES (?,?,?,?,?,?)""",
        (season, from_club_id, to_club_id, player_id, fee, transfer_type),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# /transfer_window open|close  (admin only)
# ---------------------------------------------------------------------------

@router.message(Command("transfer_window"))
async def cmd_transfer_window(message: Message):
    from config import ADMIN_IDS
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or args[1].strip().lower() not in ("open", "close"):
        await message.answer("ℹ️ Использование: /transfer_window open|close")
        return

    state = args[1].strip().lower()
    async with get_db() as db:
        from database import update_game_state
        await update_game_state(db, "transfer_window", "open" if state == "open" else "closed")

    if state == "open":
        await message.answer("🪟 Трансферное окно <b>открыто!</b>", parse_mode="HTML")
        await _post_to_group(
            message.bot,
            "🚨 <b>Трансферное окно открыто!</b>\n\nИспользуйте /transfer_internal и /buy_external.",
        )
    else:
        await message.answer("🔒 Трансферное окно <b>закрыто!</b>", parse_mode="HTML")
        await _post_to_group(message.bot, "🔒 <b>Трансферное окно закрыто!</b>")


# ---------------------------------------------------------------------------
# /transfer_internal @user <player_name> <fee_millions>
# ---------------------------------------------------------------------------

@router.message(Command("transfer_internal"))
async def cmd_transfer_internal(message: Message):
    """
    Transfer a player between two human-managed clubs.
    Usage: /transfer_internal @username PlayerName FeeInMillions
    """
    # Check transfer window
    async with get_db() as db:
        tw = await get_game_state(db, "transfer_window", "closed")
    if tw != "open":
        await message.answer("🔒 Трансферное окно закрыто! Трансферы недоступны.")
        return

    # Parse args
    parts = message.text.split(maxsplit=3)
    # /transfer_internal @username PlayerName FeeM
    if len(parts) < 4:
        await message.answer(
            "ℹ️ Использование:\n"
            "<code>/transfer_internal @продавец Имя_игрока Сумма_в_миллионах</code>\n\n"
            "Пример: <code>/transfer_internal @rival_manager Alejandro García 15</code>",
            parse_mode="HTML",
        )
        return

    seller_mention = parts[1].lstrip("@")
    player_name = parts[2]
    try:
        fee_millions = float(parts[3].replace(",", "."))
        fee = int(fee_millions * 1_000_000)
    except ValueError:
        await message.answer("❌ Неверная сумма. Укажите число в миллионах (напр. 15.5)")
        return

    buyer_user_id = message.from_user.id

    async with get_db() as db:
        # Find buyer club
        buyer_club = await get_club_by_owner(db, buyer_user_id)
        if not buyer_club:
            await message.answer("❌ У вас нет клуба. Используйте /claim_club.")
            return

        # Find seller by username
        seller_manager = None
        async with db.execute("SELECT * FROM managers") as cur:
            managers = await cur.fetchall()
        for m in managers:
            if m["username"] and seller_mention.lower() in m["username"].lower():
                seller_manager = dict(m)
                break
        if not seller_manager:
            await message.answer(f"❌ Менеджер @{seller_mention} не найден в системе.")
            return

        seller_club = await get_club(db, seller_manager["club_id"])
        if not seller_club:
            await message.answer("❌ Клуб продавца не найден.")
            return

        if seller_club["id"] == buyer_club["id"]:
            await message.answer("❌ Нельзя передать игрока в свой же клуб.")
            return

        # Find the player in the seller's squad
        async with db.execute(
            "SELECT * FROM players WHERE club_id=? AND LOWER(name) LIKE ? AND is_retired=0",
            (seller_club["id"], f"%{player_name.lower()}%"),
        ) as cur:
            player_row = await cur.fetchone()

        if not player_row:
            await message.answer(
                f"❌ Игрок «{player_name}» не найден в составе {seller_club['name']}."
            )
            return
        player = dict(player_row)

        # Check buyer budget
        if buyer_club["budget"] < fee:
            needed = _fmt_budget(fee)
            have = _fmt_budget(buyer_club["budget"])
            await message.answer(
                f"💸 Недостаточно средств!\n"
                f"Требуется: {needed}  |  У вас: {have}"
            )
            return

        # --- Execute transfer ---
        # 1. Move player to buyer's club
        await db.execute(
            "UPDATE players SET club_id=? WHERE id=?",
            (buyer_club["id"], player["id"]),
        )
        # 2. Deduct from buyer
        await db.execute(
            "UPDATE clubs SET budget=budget-? WHERE id=?",
            (fee, buyer_club["id"]),
        )
        # 3. Add to seller
        await db.execute(
            "UPDATE clubs SET budget=budget+? WHERE id=?",
            (fee, seller_club["id"]),
        )

        # 4. Record transfer
        from database import get_current_season
        season = await get_current_season(db)
        await _record_transfer(
            db, season,
            from_club_id=seller_club["id"],
            to_club_id=buyer_club["id"],
            player_id=player["id"],
            fee=fee,
            transfer_type="internal",
        )

        await db.commit()

    # 5. Generate Fabrizio Romano news
    try:
        from ai_service import generate_transfer_news
        news = await generate_transfer_news({
            "player_name": player["name"],
            "from_club": seller_club["name"],
            "to_club": buyer_club["name"],
            "fee": fee,
        })
        headline = news.headline
        body = news.body
        quote = news.fabrizio_quote
    except Exception as e:
        logger.warning(f"Transfer news generation failed: {e}")
        headline = f"🚨 {player['name']} — в {buyer_club['name']}!"
        body = f"{player['name']} переходит из {seller_club['name']} в {buyer_club['name']} за {_fmt_budget(fee)}."
        quote = "Here we go! ✅"

    fee_str = _fmt_budget(fee)
    announcement = (
        f"🔴⚪ <b>{headline}</b>\n\n"
        f"{body}\n\n"
        f"💰 Сумма трансфера: <b>{fee_str}</b>\n"
        f"📍 {player['position']} | OVR {player['ovr']} | Возраст {player['age']}\n\n"
        f"<i>«{quote}»</i>"
    )

    await message.answer(announcement, parse_mode="HTML")
    await _post_to_group(message.bot, announcement)


# ---------------------------------------------------------------------------
# /buy_external <player_name>
# ---------------------------------------------------------------------------

@router.message(Command("buy_external"))
async def cmd_buy_external(message: Message):
    """
    Purchase an external (AI-generated) player.
    Usage: /buy_external Player Name
    """
    # Check transfer window
    async with get_db() as db:
        tw = await get_game_state(db, "transfer_window", "closed")
    if tw != "open":
        await message.answer("🔒 Трансферное окно закрыто! Трансферы недоступны.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2 or not args[1].strip():
        await message.answer(
            "ℹ️ Использование: <code>/buy_external Имя игрока</code>\n"
            "Пример: <code>/buy_external Erling Haaland</code>",
            parse_mode="HTML",
        )
        return

    player_name = args[1].strip()
    user_id = message.from_user.id

    await message.answer(f"🔍 Оцениваю игрока <b>{player_name}</b>…", parse_mode="HTML")

    async with get_db() as db:
        buyer_club = await get_club_by_owner(db, user_id)
        if not buyer_club:
            await message.answer("❌ У вас нет клуба. Используйте /claim_club.")
            return

        # Valuate the player via AI
        try:
            from ai_service import valuate_external_player
            valuation = await valuate_external_player(
                player_name,
                context=f"Клуб-покупатель: {buyer_club['name']}",
            )
        except Exception as e:
            logger.error(f"Valuation error: {e}")
            await message.answer("❌ Не удалось оценить игрока. Попробуйте позже.")
            return

        markup = valuation.markup_value
        market = valuation.market_value
        ovr = valuation.ovr
        pos = valuation.position
        age = valuation.age
        actual_name = valuation.player_name or player_name

        # Show valuation to user
        await message.answer(
            f"📋 <b>Отчёт скаута</b>\n\n"
            f"👤 Игрок: <b>{actual_name}</b>\n"
            f"📍 Позиция: {pos} | ⭐ OVR: {ovr} | 🎂 Возраст: {age}\n"
            f"🌍 Национальность: {valuation.nationality}\n\n"
            f"💰 Рыночная стоимость: {_fmt_budget(market)}\n"
            f"💸 Цена покупки (с агентскими): <b>{_fmt_budget(markup)}</b>\n\n"
            f"📝 {valuation.description}",
            parse_mode="HTML",
        )

        # Check budget
        if buyer_club["budget"] < markup:
            have = _fmt_budget(buyer_club["budget"])
            need = _fmt_budget(markup)
            await message.answer(
                f"💸 <b>Недостаточно средств!</b>\n"
                f"Нужно: {need}  |  У вас: {have}\n\n"
                f"Пополните бюджет или выберите более дешёвого игрока.",
                parse_mode="HTML",
            )
            return

        # Insert player into DB
        async with db.execute(
            "INSERT INTO players (club_id, name, position, age, ovr) VALUES (?,?,?,?,?)",
            (buyer_club["id"], actual_name, pos, age, ovr),
        ) as cur:
            new_player_id = cur.lastrowid

        # Deduct budget
        await db.execute(
            "UPDATE clubs SET budget=budget-? WHERE id=?",
            (markup, buyer_club["id"]),
        )

        # Record transfer (from external = NULL)
        from database import get_current_season
        season = await get_current_season(db)
        await _record_transfer(
            db, season,
            from_club_id=None,
            to_club_id=buyer_club["id"],
            player_id=new_player_id,
            fee=markup,
            transfer_type="external",
        )

        await db.commit()

    # Generate transfer news
    try:
        from ai_service import generate_transfer_news
        news = await generate_transfer_news({
            "player_name": actual_name,
            "from_club": "свободный рынок",
            "to_club": buyer_club["name"],
            "fee": markup,
        })
        headline = news.headline
        body = news.body
        quote = news.fabrizio_quote
    except Exception as e:
        logger.warning(f"Transfer news failed: {e}")
        headline = f"🚨 {actual_name} — подписан {buyer_club['name']}!"
        body = f"{buyer_club['name']} подписал {actual_name} за {_fmt_budget(markup)}."
        quote = "Here we go! ✅"

    announcement = (
        f"🔵🟡 <b>{headline}</b>\n\n"
        f"{body}\n\n"
        f"💰 Сумма трансфера: <b>{_fmt_budget(markup)}</b>\n"
        f"📍 {pos} | OVR {ovr} | Возраст {age}\n\n"
        f"<i>«{quote}»</i>"
    )

    await message.answer(announcement, parse_mode="HTML")
    await _post_to_group(message.bot, announcement)


# ---------------------------------------------------------------------------
# /list_transfers
# ---------------------------------------------------------------------------

@router.message(Command("list_transfers"))
async def cmd_list_transfers(message: Message):
    async with get_db() as db:
        from database import get_current_season
        season = await get_current_season(db)
        async with db.execute(
            """SELECT t.*, p.name as player_name, p.position, p.ovr,
                      fc.name as from_club_name, tc.name as to_club_name
               FROM transfers t
               JOIN players p ON p.id = t.player_id
               LEFT JOIN clubs fc ON fc.id = t.from_club_id
               JOIN clubs tc ON tc.id = t.to_club_id
               WHERE t.season=?
               ORDER BY t.created_at DESC
               LIMIT 15""",
            (season,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await message.answer(f"📋 Трансферов в сезоне {season} ещё не было.")
        return

    lines = [f"📋 <b>Трансферы — Сезон {season}</b>\n"]
    for t in rows:
        fee_str = _fmt_budget(t["fee"]) if t["fee"] else "Бесплатно"
        from_name = t["from_club_name"] or "Внешний рынок"
        icon = "🔄" if t["transfer_type"] == "internal" else "🌍"
        lines.append(
            f"{icon} <b>{t['player_name']}</b> ({t['position']}, OVR {t['ovr']})\n"
            f"   {from_name} ➡️ {t['to_club_name']} | {fee_str}"
        )

    await message.answer("\n".join(lines), parse_mode="HTML")


# ---------------------------------------------------------------------------
# /free_agents
# ---------------------------------------------------------------------------

@router.message(Command("free_agents"))
async def cmd_free_agents(message: Message):
    """Display available free agents seeking contracts."""
    try:
        async with get_db() as db:
            agents = await get_free_agents(db)

        if not agents:
            await message.answer("📋 На рынке свободных агентов сейчас нет доступных игроков.")
            return

        lines = [
            f"🆓 <b>Свободные агенты ({len(agents)} игроков):</b>\n",
            "<i>Чтобы подписать контракт, используйте команду:</i>\n<code>/sign_free_agent Имя игрока</code>\n",
        ]

        # Display top 15 available free agents
        for idx, fa in enumerate(agents[:15], start=1):
            price_str = _fmt_budget(fa.get("asking_price", 5_000_000))
            lines.append(
                f"{idx}. <b>{fa['name']}</b> ({fa['position']}, OVR {fa['ovr']}) "
                f"— {fa.get('age', 24)} лет | 💰 {price_str}"
            )

        if len(agents) > 15:
            lines.append(f"\n<i>... и ещё {len(agents) - 15} игроков в базе.</i>")

        await message.answer("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        logger.error(f"Error in free_agents: {e}", exc_info=True)
        await message.answer("❌ Ошибка при получении списка свободных агентов.")


# ---------------------------------------------------------------------------
# /sign_free_agent <player_name>
# ---------------------------------------------------------------------------

@router.message(Command("sign_free_agent"))
async def cmd_sign_free_agent(message: Message):
    """Sign a free agent to the manager's club."""
    try:
        # Check transfer window
        async with get_db() as db:
            tw = await get_game_state(db, "transfer_window", "closed")
        if tw != "open":
            await message.answer("🔒 Трансферное окно закрыто! Подписание игроков недоступно.")
            return

        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("ℹ️ Использование: <code>/sign_free_agent Имя игрока</code>", parse_mode="HTML")
            return

        target_name = parts[1].strip()
        user_id = message.from_user.id

        async with get_db() as db:
            buyer_club = await get_club_by_owner(db, user_id)
            if not buyer_club:
                await message.answer("❌ У вас нет клуба! Используйте /claim_club.")
                return

            agent = await get_free_agent_by_name(db, target_name)
            if not agent:
                await message.answer(f"❌ Свободный агент с именем «{target_name}» не найден.")
                return

            price = agent.get("asking_price", 5_000_000)
            if buyer_club["budget"] < price:
                await message.answer(
                    f"💸 <b>Недостаточно средств для подписного бонуса!</b>\n"
                    f"Требуется: {_fmt_budget(price)} | У вас: {_fmt_budget(buyer_club['budget'])}",
                    parse_mode="HTML",
                )
                return

            # Add to players table
            async with db.execute(
                """INSERT INTO players (club_id, name, position, age, ovr)
                   VALUES (?,?,?,?,?)""",
                (buyer_club["id"], agent["name"], agent["position"], agent["age"], agent["ovr"]),
            ) as cur:
                new_pid = cur.lastrowid

            # Deduct fee from buyer club budget
            await db.execute(
                "UPDATE clubs SET budget=budget-? WHERE id=?",
                (price, buyer_club["id"]),
            )

            # Remove from free agents
            await delete_free_agent(db, agent["id"])

            # Record transfer
            from database import get_current_season
            season = await get_current_season(db)
            await _record_transfer(
                db, season,
                from_club_id=None,
                to_club_id=buyer_club["id"],
                player_id=new_pid,
                fee=price,
                transfer_type="free_agent",
            )
            await db.commit()

        # Try to generate Fabrizio Romano news
        actual_name = agent["name"]
        try:
            from ai_service import generate_transfer_news
            news = await generate_transfer_news({
                "player_name": actual_name,
                "from_club": "свободный агент",
                "to_club": buyer_club["name"],
                "fee": price,
            })
            headline = news.headline
            body = news.body
            quote = news.fabrizio_quote
        except Exception:
            headline = f"🚨 {actual_name} заключил контракт с {buyer_club['name']}!"
            body = f"Свободный агент {actual_name} успешно подписал соглашение с {buyer_club['name']}."
            quote = "Here we go! ✅"

        announcement = (
            f"🆓 <b>{headline}</b>\n\n"
            f"{body}\n\n"
            f"💰 Подписной бонус: <b>{_fmt_budget(price)}</b>\n"
            f"📍 {agent['position']} | OVR {agent['ovr']} | Возраст {agent['age']}\n\n"
            f"<i>«{quote}»</i>"
        )
        await message.answer(announcement, parse_mode="HTML")
        await _post_to_group(message.bot, announcement)

    except Exception as e:
        logger.error(f"Error signing free agent: {e}", exc_info=True)
        await message.answer("❌ Ошибка при подписании свободного агента.")
