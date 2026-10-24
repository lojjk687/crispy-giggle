"""
keyboards.py — La Liga AI Dynasty
Inline keyboards for all bot menus, matchday controls, club claims, and master admin panel.
Uses aiogram 3.x InlineKeyboardBuilder.
"""

from __future__ import annotations

from typing import Any, List
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ---------------------------------------------------------------------------
# Callback data prefixes
# ---------------------------------------------------------------------------
CB_TACTIC = "tactic"
CB_LATE = "late"
CB_CAPTAIN = "captain"
CB_SIMMODE = "simmode"
CB_TRAIN = "train"
CB_TRANSFER_CONFIRM = "transfer_confirm"
CB_TRANSFER_CANCEL = "transfer_cancel"
CB_PREMATCH = "prematch"
CB_CLUB = "club"
CB_STANDINGS = "standings"
CB_PANEL = "panel"
CB_CLAIM = "claim_club"


# ---------------------------------------------------------------------------
# 1. Tactics keyboard (2 × 2)
# ---------------------------------------------------------------------------
def get_tactics_keyboard(club_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🚌 Автобус", callback_data=f"{CB_TACTIC}:{club_id}:bus")
    builder.button(text="⚡ Прессинг", callback_data=f"{CB_TACTIC}:{club_id}:press")
    builder.button(text="🎯 Тики-така", callback_data=f"{CB_TACTIC}:{club_id}:tiki")
    builder.button(text="💨 Бей-беги", callback_data=f"{CB_TACTIC}:{club_id}:direct")
    builder.adjust(2)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 2. Late-game (70th minute) instruction keyboard (1 × 3)
# ---------------------------------------------------------------------------
def get_lategame_keyboard(club_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚔️ Все в атаку!", callback_data=f"{CB_LATE}:{club_id}:attack")
    builder.button(text="🛡️ Сушим игру", callback_data=f"{CB_LATE}:{club_id}:defend")
    builder.button(text="🏃 Контратаки", callback_data=f"{CB_LATE}:{club_id}:counter")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 3. Captain selection keyboard
# ---------------------------------------------------------------------------
def get_captain_keyboard(club_id: int, players: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for player in players[:10]:
        player_id = player["id"] if isinstance(player, dict) else player.id
        name = player["name"] if isinstance(player, dict) else player.name
        ovr = player["ovr"] if isinstance(player, dict) else player.ovr
        builder.button(
            text=f"{name} (OVR {ovr})",
            callback_data=f"{CB_CAPTAIN}:{club_id}:{player_id}",
        )
    builder.adjust(2)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 4. Simulation-mode keyboard
# ---------------------------------------------------------------------------
def get_simulation_mode_keyboard(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Полный репортаж", callback_data=f"{CB_SIMMODE}:{match_id}:full")
    builder.button(text="⚡ Быстрый результат", callback_data=f"{CB_SIMMODE}:{match_id}:fast")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 5. Training keyboard
# ---------------------------------------------------------------------------
def get_training_keyboard(club_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⚽ Атака", callback_data=f"{CB_TRAIN}:{club_id}:attack")
    builder.button(text="🛡️ Оборона", callback_data=f"{CB_TRAIN}:{club_id}:defense")
    builder.button(text="💪 Физподготовка", callback_data=f"{CB_TRAIN}:{club_id}:fitness")
    builder.button(text="🌱 Академия", callback_data=f"{CB_TRAIN}:{club_id}:academy")
    builder.adjust(2)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 6. Transfer confirmation keyboard
# ---------------------------------------------------------------------------
def get_transfer_confirm_keyboard(
    player_id: int,
    from_club_id: int,
    to_club_id: int,
    fee: int,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить трансфер",
        callback_data=f"{CB_TRANSFER_CONFIRM}:{player_id}:{from_club_id}:{to_club_id}:{fee}",
    )
    builder.button(
        text="❌ Отмена",
        callback_data=f"{CB_TRANSFER_CANCEL}:{player_id}",
    )
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 7. Pre-match menu keyboard
# ---------------------------------------------------------------------------
def get_prematch_menu_keyboard(club_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="👑 Выбрать капитана", callback_data=f"{CB_PREMATCH}_captain:{club_id}")
    builder.button(text="⚽ Тактика", callback_data=f"{CB_PREMATCH}_tactic:{club_id}")
    builder.button(text="🎯 План на конец игры", callback_data=f"{CB_PREMATCH}_late:{club_id}")
    builder.button(text="🗳️ Режим симуляции", callback_data=f"{CB_PREMATCH}_simmode:{club_id}")
    builder.adjust(2)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 8. Club menu keyboard
# ---------------------------------------------------------------------------
def get_club_menu_keyboard(club_id: int, is_owner: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Состав команды", callback_data=f"{CB_CLUB}_roster:{club_id}")
    builder.button(text="💰 Финансы", callback_data=f"{CB_CLUB}_finance:{club_id}")
    builder.button(text="📊 Статистика", callback_data=f"{CB_CLUB}_stats:{club_id}")
    if is_owner:
        builder.button(text="🔄 Трансфер", callback_data=f"{CB_CLUB}_transfer:{club_id}")
        builder.button(text="🏋️ Тренировка", callback_data=f"{CB_CLUB}_train:{club_id}")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 9. Copa del Rey vote keyboard
# ---------------------------------------------------------------------------
def get_copa_vote_keyboard(match_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🎬 Полный репортаж", callback_data=f"copa_{CB_SIMMODE}:{match_id}:full")
    builder.button(text="⚡ Быстрый результат", callback_data=f"copa_{CB_SIMMODE}:{match_id}:fast")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 10. Standings navigation keyboard
# ---------------------------------------------------------------------------
def get_standings_nav_keyboard(current_season: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Таблица Ла Лиги", callback_data=f"{CB_STANDINGS}:liga:{current_season}")
    builder.button(text="🏆 Кубок дель Рей", callback_data=f"{CB_STANDINGS}:copa:{current_season}")
    builder.button(text="🌟 Бомбардиры", callback_data=f"{CB_STANDINGS}:scorers:{current_season}")
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 11. Master Telegram Control Panel Keyboard (Upgrade #5)
# ---------------------------------------------------------------------------
def get_admin_panel_keyboard(autopilot_on: bool = False, transfer_window_open: bool = True) -> InlineKeyboardMarkup:
    """Master 100% Zero-Touch Autonomous Control Panel Keyboard."""
    builder = InlineKeyboardBuilder()

    # 1. Match simulation
    builder.button(
        text="⚽ Симулировать следующий тур",
        callback_data=f"{CB_PANEL}:sim_round",
    )
    # 2. League-wide training
    builder.button(
        text="🏋️‍♂️ Провести неделю тренировок",
        callback_data=f"{CB_PANEL}:train_all",
    )
    # 3. Transfer window toggle
    window_state_str = "ОТКРЫТО 🟢" if transfer_window_open else "ЗАКРЫТО 🔴"
    builder.button(
        text=f"🔄 Трансферы: {window_state_str}",
        callback_data=f"{CB_PANEL}:toggle_transfer",
    )
    # 4. End Season / Rollover
    builder.button(
        text="🏆 Церемония наград и Новый Сезон",
        callback_data=f"{CB_PANEL}:next_season",
    )
    # 5. Autopilot toggle
    auto_state_str = "ВКЛЮЧЁН 🟢" if autopilot_on else "ВЫКЛЮЧЕН ⚪"
    builder.button(
        text=f"🤖 Автопилот: {auto_state_str}",
        callback_data=f"{CB_PANEL}:toggle_autopilot",
    )
    # 6. Database backup export
    builder.button(
        text="💾 Скачать бэкап базы (.db)",
        callback_data=f"{CB_PANEL}:backup_db",
    )
    # 7. Standings
    builder.button(
        text="📊 Турнирная таблица",
        callback_data=f"{CB_PANEL}:standings",
    )

    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 12. Season Rollover Confirmation Keyboard (Upgrade #4)
# ---------------------------------------------------------------------------
def get_season_rollover_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="🚀 Завершить сезон и начать новый",
        callback_data=f"{CB_PANEL}:confirm_rollover",
    )
    builder.button(
        text="❌ Отмена",
        callback_data=f"{CB_PANEL}:cancel_rollover",
    )
    builder.adjust(1)
    return builder.as_markup()


# ---------------------------------------------------------------------------
# 13. Claim Club Keyboard (/claim_club)
# ---------------------------------------------------------------------------
def get_claim_club_keyboard(clubs: list[dict]) -> InlineKeyboardMarkup:
    """Inline buttons to choose and claim an available La Liga club."""
    builder = InlineKeyboardBuilder()
    for club in clubs:
        tier_emoji = {1: "🥇", 2: "🥈", 3: "🥉"}.get(club.get("tier", 2), "⚽")
        budget_m = int(club.get("budget", 40_000_000) / 1_000_000)
        builder.button(
            text=f"{tier_emoji} {club['name']} ({budget_m}M€)",
            callback_data=f"{CB_CLAIM}:{club['id']}",
        )
    builder.adjust(1)
    return builder.as_markup()
