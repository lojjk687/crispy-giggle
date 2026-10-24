"""
database.py - Async SQLite database layer for La Liga AI Dynasty.
Includes auto-seeding on first launch, full 38-round double round-robin
fixture engine, Copa del Rey bracket generator, and autonomous season rollover.
"""

from __future__ import annotations

import json
import random
import shutil
from datetime import datetime
from typing import Any, Optional

import aiosqlite

try:
    from config import RETIREMENT_AGE, RED_CARD_FINE, SEASONS_TOTAL
except (KeyError, ImportError):
    RETIREMENT_AGE: int = 33
    RED_CARD_FINE: float = 500_000.0
    SEASONS_TOTAL: int = 10

# ---------------------------------------------------------------------------
# Path
# ---------------------------------------------------------------------------
DB_PATH = "laliga_dynasty.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS clubs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    short_name TEXT NOT NULL,
    owner_id INTEGER DEFAULT NULL,
    budget REAL DEFAULT 40000000.0,
    tier INTEGER DEFAULT 2,
    season INTEGER DEFAULT 1,
    copa_eliminated INTEGER DEFAULT 0,
    sponsor_contract TEXT DEFAULT 'safe',
    sponsor_bonus REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS managers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    username TEXT DEFAULT NULL,
    full_name TEXT DEFAULT NULL,
    club_id INTEGER DEFAULT NULL,
    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    position TEXT NOT NULL,
    age INTEGER NOT NULL,
    ovr INTEGER NOT NULL,
    goals INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    yellow_cards INTEGER DEFAULT 0,
    red_cards INTEGER DEFAULT 0,
    suspension_rounds INTEGER DEFAULT 0,
    injury_rounds INTEGER DEFAULT 0,
    xp INTEGER DEFAULT 0,
    is_retired INTEGER DEFAULT 0,
    season_joined INTEGER DEFAULT 1,
    FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    home_club_id INTEGER NOT NULL,
    away_club_id INTEGER NOT NULL,
    home_goals INTEGER DEFAULT NULL,
    away_goals INTEGER DEFAULT NULL,
    home_possession INTEGER DEFAULT NULL,
    away_possession INTEGER DEFAULT NULL,
    home_shots INTEGER DEFAULT NULL,
    away_shots INTEGER DEFAULT NULL,
    scorers_json TEXT DEFAULT NULL,
    yellow_cards_json TEXT DEFAULT NULL,
    red_cards_json TEXT DEFAULT NULL,
    injuries_json TEXT DEFAULT NULL,
    mvp TEXT DEFAULT NULL,
    narrative TEXT DEFAULT NULL,
    is_copa INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    tactics_home TEXT DEFAULT NULL,
    tactics_away TEXT DEFAULT NULL,
    FOREIGN KEY (home_club_id) REFERENCES clubs(id),
    FOREIGN KEY (away_club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS standings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    club_id INTEGER NOT NULL,
    played INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    draws INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    gf INTEGER DEFAULT 0,
    ga INTEGER DEFAULT 0,
    points INTEGER DEFAULT 0,
    UNIQUE(season, club_id),
    FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS copa_bracket (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    round_name TEXT NOT NULL,
    club1_id INTEGER NOT NULL,
    club2_id INTEGER NOT NULL,
    winner_id INTEGER DEFAULT NULL,
    match_id INTEGER DEFAULT NULL,
    FOREIGN KEY (club1_id) REFERENCES clubs(id),
    FOREIGN KEY (club2_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    from_club_id INTEGER DEFAULT NULL,
    to_club_id INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    fee REAL NOT NULL,
    transfer_type TEXT NOT NULL,
    news_post TEXT DEFAULT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (player_id) REFERENCES players(id)
);

CREATE TABLE IF NOT EXISTS season_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    data_json TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS training_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    round INTEGER NOT NULL DEFAULT 1,
    focus TEXT NOT NULL,
    report TEXT DEFAULT NULL,
    xp_gained_json TEXT DEFAULT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (club_id) REFERENCES clubs(id)
);

CREATE TABLE IF NOT EXISTS game_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _rows_to_dicts(rows: list[aiosqlite.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

class _DbManager:
    """Async context manager wrapper supporting 'async with await get_db() as db'."""
    def __init__(self, path: str):
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    def __await__(self):
        async def _open():
            return self
        return _open().__await__()

    async def __aenter__(self) -> aiosqlite.Connection:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON")
        await self.conn.execute("PRAGMA journal_mode = WAL")
        return self.conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            await self.conn.close()

    async def close(self):
        if self.conn:
            await self.conn.close()


def get_db():
    """Return configured database manager compatible with 'async with await get_db() as db:'."""
    return _DbManager(DB_PATH)


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

async def init_db() -> None:
    """Create all tables if they do not already exist."""
    async with await get_db() as db:
        for statement in _CREATE_TABLES_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                await db.execute(stmt)
        await db.commit()



# ---------------------------------------------------------------------------
# game_state helpers
# ---------------------------------------------------------------------------

async def get_game_state(db: aiosqlite.Connection, key: str, default: Any = None) -> Any:
    """Get value from game_state table."""
    async with db.execute("SELECT value FROM game_state WHERE key = ?", (key,)) as cur:
        row = await cur.fetchone()
    return row["value"] if row else default


async def get_current_season(db: aiosqlite.Connection) -> int:
    val = await get_game_state(db, "current_season", "1")
    return int(val)


async def get_current_round(db: aiosqlite.Connection) -> int:
    val = await get_game_state(db, "current_round", "1")
    return int(val)


async def update_game_state(db: aiosqlite.Connection, key: str, value: Any) -> None:
    """Insert or update a key-value pair in game_state."""
    await db.execute(
        "INSERT INTO game_state (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    await db.commit()


# ---------------------------------------------------------------------------
# Club helpers
# ---------------------------------------------------------------------------

async def get_club(db: aiosqlite.Connection, club_id: int) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM clubs WHERE id = ?", (club_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_dict(row)


async def get_club_by_owner(db: aiosqlite.Connection, owner_id: int) -> dict[str, Any] | None:
    async with db.execute("SELECT * FROM clubs WHERE owner_id = ?", (owner_id,)) as cur:
        row = await cur.fetchone()
    if row:
        return _row_to_dict(row)

    # Check managers table as fallback and synchronize
    async with db.execute(
        "SELECT c.* FROM clubs c JOIN managers m ON m.club_id = c.id WHERE m.user_id = ?",
        (owner_id,),
    ) as cur:
        row = await cur.fetchone()
    if row:
        c_dict = _row_to_dict(row)
        await db.execute("UPDATE clubs SET owner_id = ? WHERE id = ?", (owner_id, c_dict["id"]))
        await db.commit()
        return c_dict
    return None


async def create_manager(
    db: aiosqlite.Connection,
    user_id: int,
    username: str | None = None,
    full_name: str | None = None,
    club_id: int | None = None,
) -> dict[str, Any]:
    """
    Register or update a manager in the database and link to club.
    Automatically assigns ownership of the club to the user.
    """
    await db.execute(
        """
        INSERT INTO managers (user_id, username, full_name, club_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = COALESCE(excluded.username, managers.username),
            full_name = COALESCE(excluded.full_name, managers.full_name),
            club_id = COALESCE(excluded.club_id, managers.club_id)
        """,
        (user_id, username, full_name, club_id),
    )
    if club_id is not None:
        await db.execute("UPDATE clubs SET owner_id = ? WHERE id = ?", (user_id, club_id))

    await db.commit()

    async with db.execute("SELECT * FROM managers WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_dict(row)


async def get_manager(db: aiosqlite.Connection, user_id: int) -> dict[str, Any] | None:
    """Return manager record for user_id or None."""
    async with db.execute("SELECT * FROM managers WHERE user_id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return _row_to_dict(row)


async def get_available_clubs(db: aiosqlite.Connection) -> list[dict[str, Any]]:
    """Return all clubs that do not currently have a human manager."""
    async with db.execute("SELECT * FROM clubs WHERE owner_id IS NULL ORDER BY tier, name") as cur:
        rows = await cur.fetchall()
    return _rows_to_dicts(rows)


async def update_club_budget(db: aiosqlite.Connection, club_id: int, amount: float) -> None:
    await db.execute("UPDATE clubs SET budget = budget + ? WHERE id = ?", (amount, club_id))
    await db.commit()


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

async def get_players(
    db: aiosqlite.Connection,
    club_id: int,
    include_retired: bool = False,
) -> list[dict[str, Any]]:
    if include_retired:
        sql = "SELECT * FROM players WHERE club_id = ? ORDER BY position, ovr DESC"
    else:
        sql = "SELECT * FROM players WHERE club_id = ? AND is_retired = 0 ORDER BY position, ovr DESC"

    async with db.execute(sql, (club_id,)) as cur:
        rows = await cur.fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Standings helpers
# ---------------------------------------------------------------------------

async def get_standings(db: aiosqlite.Connection, season: int) -> list[dict[str, Any]]:
    sql = """
        SELECT s.*, c.name AS club_name, c.short_name,
               (s.gf - s.ga) AS gd
        FROM standings s
        JOIN clubs c ON c.id = s.club_id
        WHERE s.season = ?
        ORDER BY s.points DESC,
                 (s.gf - s.ga) DESC,
                 s.gf DESC,
                 c.name ASC
    """
    async with db.execute(sql, (season,)) as cur:
        rows = await cur.fetchall()
    return _rows_to_dicts(rows)


# ---------------------------------------------------------------------------
# Match Result Application
# ---------------------------------------------------------------------------

async def apply_match_result(
    db: aiosqlite.Connection,
    match_id: int,
    home_goals: int,
    away_goals: int,
    scorers: list[dict],
    yellow_cards: list[dict],
    red_cards: list[dict],
    injuries: list[dict],
    narrative: str,
    possession_home: int,
    shots_home: int,
    shots_away: int,
    mvp: str,
) -> dict[str, Any]:
    async with db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)) as cur:
        match_row = await cur.fetchone()
    if match_row is None:
        raise ValueError(f"Match {match_id} not found")
    match = dict(match_row)

    home_club_id: int = match["home_club_id"]
    away_club_id: int = match["away_club_id"]
    season: int = match["season"]

    await db.execute(
        """
        UPDATE matches SET
            home_goals = ?,
            away_goals = ?,
            home_possession = ?,
            away_possession = ?,
            home_shots = ?,
            away_shots = ?,
            scorers_json = ?,
            yellow_cards_json = ?,
            red_cards_json = ?,
            injuries_json = ?,
            mvp = ?,
            narrative = ?,
            status = 'completed'
        WHERE id = ?
        """,
        (
            home_goals,
            away_goals,
            possession_home,
            100 - possession_home,
            shots_home,
            shots_away,
            json.dumps(scorers),
            json.dumps(yellow_cards),
            json.dumps(red_cards),
            json.dumps(injuries),
            mvp,
            narrative,
            match_id,
        ),
    )

    if home_goals > away_goals:
        home_w, home_d, home_l, home_pts = 1, 0, 0, 3
        away_w, away_d, away_l, away_pts = 0, 0, 1, 0
    elif home_goals < away_goals:
        home_w, home_d, home_l, home_pts = 0, 0, 1, 0
        away_w, away_d, away_l, away_pts = 1, 0, 0, 3
    else:
        home_w, home_d, home_l, home_pts = 0, 1, 0, 1
        away_w, away_d, away_l, away_pts = 0, 1, 0, 1

    for club_id, gf, ga, w, d, l, pts in (
        (home_club_id, home_goals, away_goals, home_w, home_d, home_l, home_pts),
        (away_club_id, away_goals, home_goals, away_w, away_d, away_l, away_pts),
    ):
        await db.execute(
            """
            INSERT INTO standings (season, club_id, played, wins, draws, losses, gf, ga, points)
            VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(season, club_id) DO UPDATE SET
                played = played + 1,
                wins   = wins   + excluded.wins,
                draws  = draws  + excluded.draws,
                losses = losses + excluded.losses,
                gf     = gf     + excluded.gf,
                ga     = ga     + excluded.ga,
                points = points + excluded.points
            """,
            (season, club_id, w, d, l, gf, ga, pts),
        )

    for event in scorers:
        player_id = event.get("player_id")
        if player_id:
            await db.execute("UPDATE players SET goals = goals + 1 WHERE id = ?", (player_id,))

    for yc in yellow_cards:
        player_id = yc.get("player_id")
        if player_id:
            await db.execute("UPDATE players SET yellow_cards = yellow_cards + 1 WHERE id = ?", (player_id,))
            count = sum(1 for y in yellow_cards if y.get("player_id") == player_id)
            if count >= 2:
                await db.execute("UPDATE players SET suspension_rounds = suspension_rounds + 1 WHERE id = ?", (player_id,))

    for rc in red_cards:
        player_id = rc.get("player_id")
        club_id = rc.get("club_id")
        if player_id:
            await db.execute(
                "UPDATE players SET red_cards = red_cards + 1, suspension_rounds = suspension_rounds + 1 WHERE id = ?",
                (player_id,),
            )
        if club_id:
            await db.execute("UPDATE clubs SET budget = budget - ? WHERE id = ?", (RED_CARD_FINE, club_id))

    for inj in injuries:
        player_id = inj.get("player_id")
        rounds = inj.get("rounds", 1)
        if player_id:
            await db.execute("UPDATE players SET injury_rounds = injury_rounds + ? WHERE id = ?", (rounds, player_id))

    await db.commit()
    async with db.execute("SELECT * FROM matches WHERE id = ?", (match_id,)) as cur:
        updated = await cur.fetchone()
    return _row_to_dict(updated)


# ---------------------------------------------------------------------------
# Fixture & Bracket Generators (Upgrade #2 & #4)
# ---------------------------------------------------------------------------

async def generate_season_fixtures(db: aiosqlite.Connection, season: int) -> int:
    """
    Generate double round-robin schedule (38 rounds, 380 matches) for 20 clubs.
    Uses circle polygon algorithm. Rounds 1-19 first leg; 20-38 reverse leg.
    """
    async with db.execute("SELECT id FROM clubs ORDER BY id") as cur:
        clubs = [row["id"] for row in await cur.fetchall()]

    if len(clubs) < 2:
        return 0

    await db.execute("DELETE FROM matches WHERE season = ?", (season,))

    n = len(clubs)
    teams = list(clubs)
    if n % 2 != 0:
        teams.append(None)
        n += 1

    first_half_rounds: list[list[tuple[int, int]]] = []

    for r in range(n - 1):
        round_matches = []
        for i in range(n // 2):
            t1 = teams[i]
            t2 = teams[n - 1 - i]
            if t1 is not None and t2 is not None:
                if (r + i) % 2 == 0:
                    round_matches.append((t1, t2))
                else:
                    round_matches.append((t2, t1))
        first_half_rounds.append(round_matches)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]

    match_count = 0
    # First half (rounds 1-19)
    for r_idx, r_matches in enumerate(first_half_rounds, start=1):
        for home_id, away_id in r_matches:
            await db.execute(
                """
                INSERT INTO matches (season, round, home_club_id, away_club_id, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (season, r_idx, home_id, away_id),
            )
            match_count += 1

    # Second half (rounds 20-38, reversed home/away)
    for r_idx, r_matches in enumerate(first_half_rounds, start=n):
        for home_id, away_id in r_matches:
            await db.execute(
                """
                INSERT INTO matches (season, round, home_club_id, away_club_id, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (season, r_idx, away_id, home_id),
            )
            match_count += 1

    await db.commit()
    return match_count


async def generate_copa_bracket(db: aiosqlite.Connection, season: int) -> None:
    """Generate Copa del Rey bracket for 20 clubs (4 get byes, 16 play R32)."""
    async with db.execute("SELECT id FROM clubs ORDER BY id") as cur:
        clubs = [row["id"] for row in await cur.fetchall()]

    if len(clubs) < 4:
        return

    await db.execute("DELETE FROM copa_bracket WHERE season = ?", (season,))
    shuffled = list(clubs)
    random.shuffle(shuffled)

    # 16 teams play R32 (8 matches)
    r32_teams = shuffled[4:]
    for i in range(0, len(r32_teams), 2):
        if i + 1 < len(r32_teams):
            c1 = r32_teams[i]
            c2 = r32_teams[i + 1]
            await db.execute(
                """
                INSERT INTO copa_bracket (season, round_name, club1_id, club2_id)
                VALUES (?, 'R32', ?, ?)
                """,
                (season, c1, c2),
            )
    await db.commit()


# ---------------------------------------------------------------------------
# Auto-Seeding on Launch (Upgrade #2)
# ---------------------------------------------------------------------------

async def is_db_seeded(db: aiosqlite.Connection) -> bool:
    """Check if clubs table has rows."""
    try:
        async with db.execute("SELECT COUNT(*) as c FROM clubs") as cur:
            row = await cur.fetchone()
        return bool(row and row["c"] > 0)
    except Exception:
        return False


async def auto_seed_if_needed(db: aiosqlite.Connection) -> bool:
    """
    Check if DB is empty; if so, create tables, seed 20 clubs, squads,
    Season 1 schedule (38 rounds, 380 matches), and Copa del Rey bracket.
    """
    if await is_db_seeded(db):
        # Guarantee season 1 fixtures exist even on migrated databases
        async with db.execute("SELECT COUNT(*) as c FROM matches WHERE season = 1") as cur:
            row = await cur.fetchone()
            if row and row["c"] == 0:
                await generate_season_fixtures(db, 1)
                await generate_copa_bracket(db, 1)
        return False

    from seed_laliga import seed
    await seed(db)

    await update_game_state(db, "current_season", 1)
    await update_game_state(db, "current_round", 1)
    await update_game_state(db, "autopilot_enabled", "0")
    await update_game_state(db, "transfer_window_open", "1")

    await generate_season_fixtures(db, 1)
    await generate_copa_bracket(db, 1)
    return True


# ---------------------------------------------------------------------------
# One-Click Season Rollover State Machine (Upgrade #4)
# ---------------------------------------------------------------------------

async def execute_full_season_rollover(
    db: aiosqlite.Connection,
    new_budgets: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """
    Executes the entire off-season pipeline:
    1. Increments season counter (1 to 10).
    2. Resets round counter to 1.
    3. Ages players +1, applies OVR decay to 33+, retires veterans, creates regens.
    4. Resets injury & suspension counters.
    5. Clears copa_eliminated flags.
    6. Updates club budgets from the AI budget engine.
    7. Inserts fresh standings rows.
    8. Generates a fresh 38-round double round-robin schedule and Copa del Rey bracket.
    9. Opens the Pre-Season Transfer Window.
    """
    old_season = await get_current_season(db)
    new_season = min(old_season + 1, SEASONS_TOTAL)

    # 1. Update game state
    await update_game_state(db, "current_season", new_season)
    await update_game_state(db, "current_round", 1)
    await update_game_state(db, "transfer_window_open", "1")

    # 2. Age players
    await db.execute("UPDATE players SET age = age + 1 WHERE is_retired = 0")

    # 3. OVR decay for veterans 33+
    async with db.execute(
        "SELECT id, ovr FROM players WHERE age >= ? AND is_retired = 0",
        (RETIREMENT_AGE,),
    ) as cur:
        veterans = await cur.fetchall()

    for v in veterans:
        decay = random.randint(1, 3)
        await db.execute("UPDATE players SET ovr = ? WHERE id = ?", (max(v["ovr"] - decay, 40), v["id"]))

    # 4. Retire players >= 36 (70% probability) or OVR < 55
    async with db.execute("SELECT id, age, ovr FROM players WHERE is_retired = 0") as cur:
        all_active = await cur.fetchall()

    retired_count = 0
    for p in all_active:
        should_retire = False
        if p["ovr"] < 55:
            should_retire = True
        elif p["age"] >= 36:
            should_retire = random.random() < 0.70

        if should_retire:
            await db.execute("UPDATE players SET is_retired = 1 WHERE id = ?", (p["id"],))
            retired_count += 1

    # 5. Reset injuries & suspensions
    await db.execute("UPDATE players SET injury_rounds = 0, suspension_rounds = 0")

    # 6. Generate 2-3 regen wonderkids (16-18 y.o.) per club
    async with db.execute("SELECT id, tier FROM clubs") as cur:
        clubs = await cur.fetchall()

    POSITIONS = ["GK", "CB", "LB", "RB", "CDM", "CM", "CAM", "LW", "RW", "ST"]
    FIRST_NAMES = ["Alejandro", "Carlos", "David", "Diego", "Eduardo", "Hugo", "Javier", "Lucas", "Mateo", "Pablo"]
    LAST_NAMES = ["García", "Rodríguez", "López", "Sánchez", "Pérez", "Gómez", "Martín", "Ruiz", "Navarro"]

    regens_created = 0
    for club in clubs:
        count = random.randint(2, 3)
        tier = club["tier"]
        ovr_min = {1: 60, 2: 57, 3: 55}.get(tier, 55)
        ovr_max = {1: 68, 2: 66, 3: 64}.get(tier, 64)
        for _ in range(count):
            name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
            pos = random.choice(POSITIONS)
            age = random.randint(16, 18)
            ovr = random.randint(ovr_min, ovr_max)
            await db.execute(
                """
                INSERT INTO players (club_id, name, position, age, ovr, season_joined)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (club["id"], name, pos, age, ovr, new_season),
            )
            regens_created += 1

    # 7. Reset copa eliminated
    await db.execute("UPDATE clubs SET copa_eliminated = 0")

    # 8. Apply new budgets from AI engine if provided
    if new_budgets:
        for ident, bud in new_budgets.items():
            if isinstance(ident, int):
                await db.execute("UPDATE clubs SET budget = ? WHERE id = ?", (bud, ident))
            else:
                await db.execute("UPDATE clubs SET budget = ? WHERE name = ? OR short_name = ?", (bud, str(ident), str(ident)))

    # 9. Fresh standings
    for club in clubs:
        await db.execute(
            "INSERT OR IGNORE INTO standings (season, club_id) VALUES (?, ?)",
            (new_season, club["id"]),
        )

    # 10. Generate full fixture list for new season
    matches_created = await generate_season_fixtures(db, new_season)
    await generate_copa_bracket(db, new_season)

    summary = {
        "old_season": old_season,
        "new_season": new_season,
        "players_retired": retired_count,
        "regens_created": regens_created,
        "matches_created": matches_created,
        "timestamp": datetime.utcnow().isoformat(),
    }
    await db.execute(
        "INSERT INTO season_logs (season, event_type, data_json) VALUES (?, ?, ?)",
        (old_season, "season_end", json.dumps(summary)),
    )
    await db.commit()
    return summary


# ---------------------------------------------------------------------------
# Backup helper
# ---------------------------------------------------------------------------

def backup_db(target_path: str) -> None:
    shutil.copy2(DB_PATH, target_path)


# Backward-compatibility alias
async def end_season(db: aiosqlite.Connection) -> dict[str, Any]:
    return await execute_full_season_rollover(db)
