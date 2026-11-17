"""handlers/national_team.py — National teams, call-ups, and international tournaments."""

import logging
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from config import ADMIN_IDS, GROUP_CHAT_ID
from database import (
    assign_national_team_manager,
    call_up_player,
    end_international_break,
    get_current_season,
    get_db,
    get_game_state,
    get_national_squad,
    get_national_team_by_id,
    get_national_team_by_manager,
    get_national_teams,
    get_player_by_name,
    release_player_from_national_team,
    start_international_break,
)
from keyboards import get_national_team_keyboard

router = Router()
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# /claim_national_team
# ---------------------------------------------------------------------------

@router.message(Command("claim_national_team"))
async def cmd_claim_national_team(message: Message):
    """Allow a user to take charge of an unassigned national team."""
    try:
        user_id = message.from_user.id
        async with get_db() as db:
            current_team = await get_national_team_by_manager(db, user_id)
            if current_team:
                await message.answer(
                    f"⚠️ Вы уже руководите сборной <b>{current_team.get('emoji', '🏳️')} {current_team['country']}</b>!\n"
                    f"Используйте /national_squad чтобы увидеть состав.",
                    parse_mode="HTML",
                )
                return

            teams = await get_national_teams(db)
            available = [t for t in teams if not t.get("manager_user_id")]

        if not available:
            await message.answer(
                "🌍 Все сборные на данный момент уже заняты другими тренерами!",
                parse_mode="HTML",
            )
            return

        await message.answer(
            "🌍 <b>Выберите национальную сборную:</b>\n"
            "Вы можете одновременно тренировать и клуб, и сборную!",
            parse_mode="HTML",
            reply_markup=get_national_team_keyboard(available),
        )
    except Exception as e:
        logger.error(f"Error in claim_national_team: {e}", exc_info=True)
        await message.answer("❌ Произошла ошибка. Попробуйте позже.")


@router.callback_query(F.data.startswith("claim_nat:"))
async def cb_claim_national_team(callback: CallbackQuery):
    """Handle national team selection callback."""
    try:
        team_id = int(callback.data.split(":")[1])
        user_id = callback.from_user.id

        async with get_db() as db:
            existing = await get_national_team_by_manager(db, user_id)
            if existing:
                await callback.answer("У вас уже есть сборная!", show_alert=True)
                return

            team = await get_national_team_by_id(db, team_id)
            if not team:
                await callback.answer("Сборная не найдена.", show_alert=True)
                return
            if team.get("manager_user_id"):
                await callback.answer("Эту сборную уже забрал другой тренер!", show_alert=True)
                return

            await assign_national_team_manager(db, team_id, user_id)

        flag = team.get("emoji", "🏳️")
        country = team["country"]
        await callback.answer(f"Поздравляем! Вы возглавили сборную {country}!", show_alert=True)
        await callback.message.edit_text(
            f"🌍 {flag} <b>Вы назначены главным тренером сборной {country}!</b>\n\n"
            f"📋 Теперь вы можете:\n"
            f"• /national_squad — посмотреть текущий состав\n"
            f"• /callup &lt;Имя игрока&gt; — вызвать игрока из Ла Лиги\n"
            f"• /release_player &lt;Имя игрока&gt; — исключить игрока из заявки\n"
            f"• /my_national_team — карточка сборной\n\n"
            f"Напоминание: тренерство сборной не мешает руководству вашим клубом! 🤝",
            parse_mode="HTML",
        )

        try:
            user_mention = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.first_name
            await callback.bot.send_message(
                GROUP_CHAT_ID,
                f"🌍 <b>ОФИЦИАЛЬНО:</b> {user_mention} назначен главным тренером сборной <b>{flag} {country}</b>! 🎉",
                parse_mode="HTML",
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Error assigning national team: {e}", exc_info=True)
        await callback.answer("Ошибка при назначении.", show_alert=True)


# ---------------------------------------------------------------------------
# /my_national_team
# ---------------------------------------------------------------------------

@router.message(Command("my_national_team"))
async def cmd_my_national_team(message: Message):
    """Show details of the user's managed national team."""
    try:
        user_id = message.from_user.id
        async with get_db() as db:
            team = await get_national_team_by_manager(db, user_id)
            if not team:
                await message.answer(
                    "🌍 Вы пока не руководите сборной.\n"
                    "Выберите свободную сборную через команду /claim_national_team!"
                )
                return

            squad = await get_national_squad(db, team["id"])
            intl_state = await get_game_state(db, "intl_break", "off")

        flag = team.get("emoji", "🏳️")
        country = team["country"]
        status_txt = "🔴 Активен перерыв на сборные!" if intl_state == "on" else "⚪ Игроки находятся в клубах Ла Лиги"

        text = (
            f"🌍 {flag} <b>Сборная {country}</b>\n\n"
            f"👤 Главный тренер: <b>{message.from_user.first_name}</b>\n"
            f"📐 Тактическая схема: <b>{team.get('formation', '4-3-3')}</b>\n"
            f"👥 Игроков в заявке: <b>{len(squad)} / 23</b>\n"
            f"📅 Статус перерыва: {status_txt}\n\n"
            f"Команды:\n"
            f"• /national_squad — полный список вызванных\n"
            f"• /callup Имя — вызвать игрока\n"
            f"• /release_player Имя — убрать из заявки"
        )
        await message.answer(text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in my_national_team: {e}", exc_info=True)
        await message.answer("❌ Ошибка при получении данных о сборной.")


# ---------------------------------------------------------------------------
# /national_squad
# ---------------------------------------------------------------------------

@router.message(Command("national_squad"))
async def cmd_national_squad(message: Message):
    """View the roster of called-up players."""
    try:
        user_id = message.from_user.id
        async with get_db() as db:
            team = await get_national_team_by_manager(db, user_id)
            if not team:
                await message.answer("🌍 Сначала выберите сборную: /claim_national_team")
                return

            squad = await get_national_squad(db, team["id"])

        flag = team.get("emoji", "🏳️")
        country = team["country"]

        if not squad:
            await message.answer(
                f"🌍 {flag} <b>Заявка сборной {country} пуста!</b>\n\n"
                f"Вызовите лучших игроков лиги с помощью команды:\n"
                f"<code>/callup Имя игрока</code>",
                parse_mode="HTML",
            )
            return

        lines = [f"🌍 {flag} <b>Заявка сборной {country} ({len(squad)}/23):</b>\n"]
        for idx, p in enumerate(squad, start=1):
            club = p.get("club_name", "Свободный агент")
            inj = " 🏥" if p.get("injury_rounds", 0) > 0 else ""
            card = " 🟨" if p.get("yellow_cards", 0) > 0 else ""
            lines.append(
                f"{idx}. <b>{p['name']}</b> ({p['position']}, OVR {p['ovr']}) — {club}{inj}{card}"
            )

        await message.answer("\n".join(lines), parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in national_squad: {e}", exc_info=True)
        await message.answer("❌ Ошибка при получении заявки.")


# ---------------------------------------------------------------------------
# /callup <player_name>
# ---------------------------------------------------------------------------

@router.message(Command("callup"))
async def cmd_callup(message: Message):
    """Call up a player to the manager's national team."""
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("ℹ️ Использование: <code>/callup Имя игрока</code>", parse_mode="HTML")
            return

        p_name = parts[1].strip()
        user_id = message.from_user.id

        async with get_db() as db:
            team = await get_national_team_by_manager(db, user_id)
            if not team:
                await message.answer("🌍 У вас нет национальной сборной! Выберите через /claim_national_team")
                return

            squad = await get_national_squad(db, team["id"])
            if len(squad) >= 23:
                await message.answer("❌ Заявка заполнена! Максимум 23 игрока. Сначала отпустите кого-то: /release_player")
                return

            player = await get_player_by_name(db, p_name)
            if not player:
                await message.answer(f"❌ Игрок «{p_name}» не найден в Ла Лиге.")
                return

            # Check if already in squad
            if any(p["id"] == player["id"] for p in squad):
                await message.answer(f"⚠️ {player['name']} уже находится в заявке вашей сборной!")
                return

            await call_up_player(db, team["id"], player["id"])

        flag = team.get("emoji", "🏳️")
        await message.answer(
            f"✅ {flag} <b>{player['name']}</b> ({player['position']}, OVR {player['ovr']}) успешно вызван в сборную <b>{team['country']}</b>!",
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Error calling up player: {e}", exc_info=True)
        await message.answer("❌ Ошибка при вызове игрока.")


# ---------------------------------------------------------------------------
# /release_player <player_name>
# ---------------------------------------------------------------------------

@router.message(Command("release_player"))
async def cmd_release_player(message: Message):
    """Release a player from national team roster."""
    try:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            await message.answer("ℹ️ Использование: <code>/release_player Имя игрока</code>", parse_mode="HTML")
            return

        p_name = parts[1].strip()
        user_id = message.from_user.id

        async with get_db() as db:
            team = await get_national_team_by_manager(db, user_id)
            if not team:
                await message.answer("🌍 У вас нет национальной сборной!")
                return

            player = await get_player_by_name(db, p_name)
            if not player:
                await message.answer(f"❌ Игрок «{p_name}» не найден.")
                return

            await release_player_from_national_team(db, team["id"], player["id"])

        flag = team.get("emoji", "🏳️")
        await message.answer(
            f"👋 {flag} <b>{player['name']}</b> исключён из заявки сборной {team['country']}.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Error releasing player: {e}", exc_info=True)
        await message.answer("❌ Ошибка при исключении игрока.")


# ---------------------------------------------------------------------------
# Admin: /international_break & /end_international_break
# ---------------------------------------------------------------------------

@router.message(Command("international_break"))
async def cmd_international_break(message: Message):
    """Admin command to trigger or advance an international break."""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Только для администраторов.")
        return

    try:
        async with get_db() as db:
            current_state = await get_game_state(db, "intl_break", "off")
            season = await get_current_season(db)

            if current_state == "on":
                await end_international_break(db)
                await message.answer("✅ <b>Перерыв на сборные завершён!</b> Матчи Ла Лиги возобновлены.", parse_mode="HTML")
                await message.bot.send_message(
                    GROUP_CHAT_ID,
                    "⚽ <b>Клубный футбол возвращается!</b>\nПерерыв на матчи сборных завершён. Регулярный чемпионат Ла Лиги продолжается!",
                    parse_mode="HTML",
                )
            else:
                matches_count = await start_international_break(db, season)
                await message.answer(
                    f"🌍 <b>Объявлен перерыв на матчи сборных!</b>\nСформировано международных матчей: {matches_count}.",
                    parse_mode="HTML",
                )
                await message.bot.send_message(
                    GROUP_CHAT_ID,
                    "🌍 <b>Внимание: Перерыв на матчи сборных!</b> ✈️\n\n"
                    "Матчи клубов Ла Лиги временно приостановлены. Главные тренеры сборных формируют составы и проводят международные встречи!\n\n"
                    "Проверьте состав: /national_squad",
                    parse_mode="HTML",
                )
    except Exception as e:
        logger.error(f"Error toggling intl break: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")
