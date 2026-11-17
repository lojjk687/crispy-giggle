"""Telegram inline keyboard builders for the La Liga Dynasty bot."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📅 Симулировать тур", callback_data="admin:simulate_round"),
        InlineKeyboardButton(text="📊 Таблица", callback_data="admin:standings"),
    )
    builder.row(
        InlineKeyboardButton(text="🏆 Следующий сезон", callback_data="admin:next_season"),
        InlineKeyboardButton(text="🤖 Автопилот: ВКЛ", callback_data="admin:autopilot_on"),
    )
    builder.row(
        InlineKeyboardButton(text="🤖 Автопилот: ВЫКЛ", callback_data="admin:autopilot_off"),
        InlineKeyboardButton(text="💾 Бэкап БД", callback_data="admin:backup"),
    )
    builder.row(
        InlineKeyboardButton(text="🪟 Трансферное окно: открыть", callback_data="admin:tw_open"),
        InlineKeyboardButton(text="🚫 Трансферное окно: закрыть", callback_data="admin:tw_close"),
    )
    builder.row(
        InlineKeyboardButton(text="🎖 Церемония наград", callback_data="admin:awards"),
        InlineKeyboardButton(text="🔄 Сброс сезона", callback_data="admin:reset_season_confirm"),
    )
    return builder.as_markup()


def get_reset_season_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard before wiping season data."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚠️ Да, сбросить в Сезон 1", callback_data="admin:do_reset_season"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="admin:cancel_reset"),
    )
    return builder.as_markup()


def get_claim_club_keyboard(clubs: list[dict]) -> InlineKeyboardMarkup:
    """Build keyboard with one button per available club."""
    builder = InlineKeyboardBuilder()
    for club in clubs:
        name = club.get("name", "?")
        budget = club.get("budget", 0)
        budget_m = budget // 1_000_000
        cid = club.get("id")
        builder.row(
            InlineKeyboardButton(
                text=f"⚽ {name} — {budget_m}M€",
                callback_data=f"claim:{cid}",
            )
        )
    return builder.as_markup()


# Full tactics dict with emoji
TACTICS = {
    "4-4-2":   {"name": "4-4-2 Классика",    "desc": "Баланс атаки и обороны",      "emoji": "⚖️"},
    "4-3-3":   {"name": "4-3-3 Атакующий",   "desc": "Высокий прессинг, крылья",    "emoji": "⚡"},
    "3-5-2":   {"name": "3-5-2 Контроль",    "desc": "Доминирование в центре",      "emoji": "🎯"},
    "4-2-3-1": {"name": "4-2-3-1 Современный","desc": "Два опорника, десятка",      "emoji": "🧠"},
    "5-3-2":   {"name": "5-3-2 Автобус",     "desc": "Максимальная оборона",        "emoji": "🛡"},
    "4-1-4-1": {"name": "4-1-4-1 Прессинг",  "desc": "Тотальный прессинг",         "emoji": "🔥"},
    "3-4-3":   {"name": "3-4-3 Атака",       "desc": "Три нападающих",             "emoji": "⚔️"},
}

LATE_GAME_PLANS = {
    "attack":  {"name": "Все в атаку",      "emoji": "⚔️"},
    "defend":  {"name": "Удержать счёт",    "emoji": "🛡"},
    "counter": {"name": "Контратаки",       "emoji": "⚡"},
    "normal":  {"name": "Без изменений",    "emoji": "⚽"},
}


def get_tactics_keyboard(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for formation, info in TACTICS.items():
        builder.button(
            text=f"{info['emoji']} {formation}",
            callback_data=f"tactic:{match_id}:{formation}",
        )
    builder.adjust(2)
    return builder.as_markup()


def get_captain_keyboard(match_id: int, players: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for player in players[:10]:  # Max 10 candidates
        name = player.get("name", "?")
        ovr = player.get("ovr", 70)
        builder.button(
            text=f"© {name} (OVR {ovr})",
            callback_data=f"captain:{match_id}:{name}",
        )
    builder.adjust(2)
    return builder.as_markup()


def get_lategame_keyboard(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for plan_key, info in LATE_GAME_PLANS.items():
        builder.button(
            text=f"{info['emoji']} {info['name']}",
            callback_data=f"lategame:{match_id}:{plan_key}",
        )
    builder.adjust(2)
    return builder.as_markup()


def get_simulation_mode_keyboard(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🤖 Авто (быстро)", callback_data=f"simmode:{match_id}:fast"),
        InlineKeyboardButton(text="📖 Детальный репортаж", callback_data=f"simmode:{match_id}:detailed"),
    )
    return builder.as_markup()


def get_prematch_menu_keyboard(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📋 Тактика", callback_data=f"prematch_tactic:{match_id}"),
        InlineKeyboardButton(text="©️ Капитан", callback_data=f"prematch_captain:{match_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="🕹 Режим симуляции", callback_data=f"prematch_simmode:{match_id}"),
        InlineKeyboardButton(text="⚡ Концовка", callback_data=f"prematch_lategame:{match_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="▶️ Начать матч", callback_data=f"prematch_go:{match_id}"),
    )
    return builder.as_markup()


def get_sponsor_accept_keyboard(offers: list[dict]) -> InlineKeyboardMarkup:
    """Build keyboard with sponsor offer accept buttons."""
    builder = InlineKeyboardBuilder()
    tier_emoji = {"bronze": "🥉", "silver": "🥈", "gold": "🥇", "platinum": "💎"}
    for offer in offers:
        tier = offer.get("tier", "bronze")
        emoji = tier_emoji.get(tier, "📋")
        name = offer.get("sponsor_name", "?")
        bonus_m = offer.get("bonus_amount", 0) // 1_000_000
        builder.row(
            InlineKeyboardButton(
                text=f"{emoji} {name} (+{bonus_m}M€ бонус)",
                callback_data=f"sponsor_accept:{offer['id']}",
            )
        )
    return builder.as_markup()


def get_national_team_keyboard(teams: list[dict]) -> InlineKeyboardMarkup:
    """Keyboard to claim a national team."""
    builder = InlineKeyboardBuilder()
    for team in teams:
        status = "✅ Занято" if team.get("manager_user_id") else "🆓 Свободно"
        builder.row(
            InlineKeyboardButton(
                text=f"{team.get('emoji', '🏳️')} {team['country']} — {status}",
                callback_data=f"claim_nt:{team['id']}",
            )
        )
    return builder.as_markup()


def get_friendly_response_keyboard(match_id: int) -> InlineKeyboardMarkup:
    """Accept/decline friendly match keyboard."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Принять вызов!", callback_data=f"friendly_accept:{match_id}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"friendly_decline:{match_id}"),
    )
    return builder.as_markup()
