import aiosqlite
import asyncio
import json
import logging
import random
from contextlib import asynccontextmanager
from typing import Optional

from config import DB_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_db():
    """Async context manager that yields an aiosqlite connection."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

async def init_db():
    """Create all tables if they do not already exist."""
    async with get_db() as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS clubs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            short_name  TEXT NOT NULL,
            owner_id    INTEGER DEFAULT NULL,
            budget      INTEGER DEFAULT 30000000,
            season_budget INTEGER DEFAULT 30000000,
            tier        INTEGER DEFAULT 3,
            trophies    INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS players (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id          INTEGER NOT NULL,
            name             TEXT NOT NULL,
            position         TEXT NOT NULL,
            age              INTEGER DEFAULT 22,
            ovr              INTEGER DEFAULT 70,
            goals            INTEGER DEFAULT 0,
            assists          INTEGER DEFAULT 0,
            yellow_cards     INTEGER DEFAULT 0,
            red_cards        INTEGER DEFAULT 0,
            injury_rounds    INTEGER DEFAULT 0,
            suspension_rounds INTEGER DEFAULT 0,
            is_retired       INTEGER DEFAULT 0,
            FOREIGN KEY (club_id) REFERENCES clubs(id)
        );

        CREATE TABLE IF NOT EXISTS managers (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER UNIQUE NOT NULL,
            club_id   INTEGER NOT NULL,
            username  TEXT DEFAULT '',
            FOREIGN KEY (club_id) REFERENCES clubs(id)
        );

        CREATE TABLE IF NOT EXISTS standings (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            season   INTEGER NOT NULL,
            club_id  INTEGER NOT NULL,
            played   INTEGER DEFAULT 0,
            wins     INTEGER DEFAULT 0,
            draws    INTEGER DEFAULT 0,
            losses   INTEGER DEFAULT 0,
            gf       INTEGER DEFAULT 0,
            ga       INTEGER DEFAULT 0,
            points   INTEGER DEFAULT 0,
            UNIQUE(season, club_id),
            FOREIGN KEY (club_id) REFERENCES clubs(id)
        );

        CREATE TABLE IF NOT EXISTS matches (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            season           INTEGER NOT NULL,
            round            INTEGER NOT NULL,
            home_club_id     INTEGER NOT NULL,
            away_club_id     INTEGER NOT NULL,
            home_goals       INTEGER DEFAULT NULL,
            away_goals       INTEGER DEFAULT NULL,
            home_possession  INTEGER DEFAULT 50,
            away_possession  INTEGER DEFAULT 50,
            home_shots       INTEGER DEFAULT 0,
            away_shots       INTEGER DEFAULT 0,
            scorers_json     TEXT DEFAULT '[]',
            yellow_cards_json TEXT DEFAULT '[]',
            red_cards_json   TEXT DEFAULT '[]',
            injuries_json    TEXT DEFAULT '[]',
            mvp              TEXT DEFAULT '',
            narrative        TEXT DEFAULT '',
            status           TEXT DEFAULT 'scheduled',
            tactics_home     TEXT DEFAULT '',
            tactics_away     TEXT DEFAULT '',
            captain_home     TEXT DEFAULT '',
            captain_away     TEXT DEFAULT '',
            FOREIGN KEY (home_club_id) REFERENCES clubs(id),
            FOREIGN KEY (away_club_id) REFERENCES clubs(id)
        );

        CREATE TABLE IF NOT EXISTS game_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS transfers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            season        INTEGER NOT NULL,
            from_club_id  INTEGER DEFAULT NULL,
            to_club_id    INTEGER NOT NULL,
            player_id     INTEGER NOT NULL,
            fee           INTEGER DEFAULT 0,
            transfer_type TEXT DEFAULT 'internal',
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS copa_matches (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            season       INTEGER NOT NULL,
            round_name   TEXT NOT NULL,
            home_club_id INTEGER NOT NULL,
            away_club_id INTEGER NOT NULL,
            home_goals   INTEGER DEFAULT NULL,
            away_goals   INTEGER DEFAULT NULL,
            status       TEXT DEFAULT 'scheduled'
        );

        CREATE TABLE IF NOT EXISTS training_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id    INTEGER NOT NULL,
            season     INTEGER NOT NULL,
            round_num  INTEGER NOT NULL,
            focus      TEXT NOT NULL,
            report     TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sponsor_contracts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id         INTEGER NOT NULL UNIQUE,
            sponsor_name    TEXT NOT NULL,
            target_position INTEGER NOT NULL,
            bonus_amount    INTEGER NOT NULL,
            risk_level      TEXT DEFAULT 'medium'
        );

        CREATE TABLE IF NOT EXISTS free_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            position TEXT NOT NULL,
            age INTEGER DEFAULT 24,
            ovr INTEGER DEFAULT 70,
            nationality TEXT DEFAULT 'испанец',
            asking_price INTEGER DEFAULT 5000000,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sponsor_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            club_id INTEGER NOT NULL,
            sponsor_name TEXT NOT NULL,
            tier TEXT NOT NULL,
            target_position INTEGER NOT NULL,
            bonus_amount INTEGER NOT NULL,
            weekly_income INTEGER DEFAULT 0,
            is_accepted INTEGER DEFAULT 0,
            offered_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (club_id) REFERENCES clubs(id)
        );

        CREATE TABLE IF NOT EXISTS friendly_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            home_club_id INTEGER NOT NULL,
            away_club_id INTEGER NOT NULL,
            home_goals INTEGER DEFAULT NULL,
            away_goals INTEGER DEFAULT NULL,
            narrative TEXT DEFAULT '',
            status TEXT DEFAULT 'scheduled',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS national_teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            country TEXT NOT NULL UNIQUE,
            emoji TEXT DEFAULT '🏳️',
            manager_user_id INTEGER DEFAULT NULL,
            formation TEXT DEFAULT '4-3-3'
        );

        CREATE TABLE IF NOT EXISTS national_team_squads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            national_team_id INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            is_called_up INTEGER DEFAULT 0,
            UNIQUE(national_team_id, player_id),
            FOREIGN KEY (national_team_id) REFERENCES national_teams(id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        );

        CREATE TABLE IF NOT EXISTS international_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            season INTEGER NOT NULL,
            home_team_id INTEGER NOT NULL,
            away_team_id INTEGER NOT NULL,
            home_goals INTEGER DEFAULT NULL,
            away_goals INTEGER DEFAULT NULL,
            narrative TEXT DEFAULT '',
            status TEXT DEFAULT 'scheduled',
            is_break INTEGER DEFAULT 1
        );
        """)
        await db.commit()
    logger.info("Database initialised.")


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------

_CLUB_SEED = [
    # (name, short_name, budget, tier)
    ("Real Madrid",      "RMA", 120_000_000, 1),
    ("Barcelona",        "BAR", 120_000_000, 1),
    ("Atletico Madrid",  "ATM",  80_000_000, 1),
    ("Sevilla",          "SEV",  55_000_000, 2),
    ("Real Betis",       "BET",  45_000_000, 2),
    ("Real Sociedad",    "RSO",  45_000_000, 2),
    ("Villarreal",       "VIL",  45_000_000, 2),
    ("Athletic Club",    "ATH",  40_000_000, 2),
    ("Valencia",         "VAL",  38_000_000, 2),
    ("Osasuna",          "OSA",  30_000_000, 3),
    ("Celta Vigo",       "CEL",  30_000_000, 3),
    ("Getafe",           "GET",  28_000_000, 3),
    ("Rayo Vallecano",   "RAY",  25_000_000, 3),
    ("Almeria",          "ALM",  25_000_000, 3),
    ("Cadiz",            "CAD",  22_000_000, 3),
    ("Girona",           "GIR",  35_000_000, 2),
    ("Las Palmas",       "PAL",  22_000_000, 3),
    ("Granada",          "GRA",  22_000_000, 3),
    ("Alaves",           "ALA",  22_000_000, 3),
    ("Mallorca",         "MAL",  28_000_000, 3),
]

_POSITIONS = ["GK", "GK", "CB", "CB", "CB", "CB", "LB", "RB", "CDM", "CDM",
              "CM", "CM", "CAM", "LW", "RW", "ST", "ST", "Utility", "Utility", "Utility"]

_SPANISH_NAMES = [
    "Alejandro García", "Carlos Martínez", "David López", "Eduardo Sánchez",
    "Fernando Rodríguez", "Gabriel Torres", "Héctor Ramírez", "Ignacio Flores",
    "Javier Morales", "Kevin Hernández", "Luis Castro", "Miguel Ortega",
    "Nicolás Ruiz", "Oscar Mendez", "Pablo Álvarez", "Raúl Jiménez",
    "Sergio Navarro", "Tomás Vega", "Víctor Molina", "Xavier Suárez",
    "Álvaro Reyes", "Bruno Santos", "César Cabrera", "Diego Guerrero",
    "Emilio Pardo", "Felipe Blanco", "Gonzalo Ramos", "Humberto Silva",
    "Iván Domínguez", "Jorge Peña", "Luca Ferreira", "Marco Delgado",
    "Noel Medina", "Pedro Acosta", "Quique Serrano", "Roberto Ibáñez",
    "Samuel Vidal", "Teodoro Lara", "Umberto Cano", "Valentín Prieto",
    "Ahmed Benali", "Bojan Petrić", "Carlos Tevita", "Dani Olmo",
    "Erling Silva", "Fede Valverde", "Gavi Puig", "Hamza Riad",
    "Iker Barrenetxea", "Jao Félix",
]


def _get_ovr_range(tier: int, position: str) -> tuple[int, int]:
    if tier == 1:
        return (78, 90)
    elif tier == 2:
        return (72, 82)
    else:
        return (65, 76)


def _make_players_for_club(club_id: int, tier: int) -> list[dict]:
    players = []
    used_names = set()
    name_pool = _SPANISH_NAMES[:]
    random.shuffle(name_pool)

    for i, pos in enumerate(_POSITIONS):
        # Pick unique name
        name = None
        for n in name_pool:
            if n not in used_names:
                name = n
                used_names.add(n)
                break
        if name is None:
            name = f"Jugador {club_id}-{i}"

        lo, hi = _get_ovr_range(tier, pos)
        ovr = random.randint(lo, hi)
        age = random.randint(18, 33)
        players.append({
            "club_id": club_id,
            "name": name,
            "position": pos,
            "age": age,
            "ovr": ovr,
        })
    return players


async def _seed_free_agents(db):
    """Seed 30 free agents with realistic stats."""
    free_agents_data = [
        ("Marco Verratti", "CM", 31, 83, "итальянец", 8_000_000),
        ("Isco Alarcón", "CAM", 32, 80, "испанец", 5_000_000),
        ("Jesé Rodríguez", "LW", 30, 72, "испанец", 3_000_000),
        ("Roberto Soldado", "ST", 38, 65, "испанец", 2_000_000),
        ("Nabil Fekir", "CAM", 31, 81, "француз", 9_000_000),
        ("Memphis Depay", "ST", 30, 82, "нидерландец", 10_000_000),
        ("Alexis Sánchez", "ST", 35, 75, "чилиец", 4_000_000),
        ("Ousmane Dembélé", "RW", 27, 85, "француз", 20_000_000),
        ("Philippe Coutinho", "CAM", 32, 78, "бразилец", 6_000_000),
        ("Gareth Bale", "RW", 34, 70, "валлиец", 3_000_000),
        ("Mario Balotelli", "ST", 34, 68, "итальянец", 2_000_000),
        ("Zlatan Ibrahimovic", "ST", 42, 70, "швед", 2_500_000),
        ("Javier Pastore", "CAM", 35, 68, "аргентинец", 2_000_000),
        ("Lassana Diarra", "CDM", 39, 65, "француз", 2_000_000),
        ("Mikel Arteta", "CM", 28, 76, "испанец", 7_000_000),
        ("Carlos Soler", "CM", 27, 78, "испанец", 12_000_000),
        ("Dani Olmo", "CAM", 26, 83, "испанец", 18_000_000),
        ("Yerlan Azhgaliyev", "CM", 24, 71, "казахстанец", 5_000_000),
        ("Adam Ounas", "LW", 27, 74, "алжирец", 6_000_000),
        ("Sofiane Feghouli", "RW", 34, 69, "алжирец", 2_500_000),
        ("Ryad Boudebouz", "CAM", 33, 71, "алжирец", 3_000_000),
        ("Fabian Ruiz", "CM", 28, 81, "испанец", 15_000_000),
        ("Mikel Merino", "CDM", 28, 80, "испанец", 14_000_000),
        ("Rodrigo Moreno", "ST", 32, 77, "испанец", 8_000_000),
        ("Paco Alcácer", "ST", 31, 75, "испанец", 5_000_000),
        ("Denis Suárez", "CM", 30, 73, "испанец", 4_000_000),
        ("Suso Fernández", "RW", 30, 74, "испанец", 4_500_000),
        ("Vitolo", "LW", 34, 69, "испанец", 2_000_000),
        ("Stefan Savić", "CB", 33, 78, "черногорец", 7_000_000),
        ("Diego Godín", "CB", 38, 71, "уругваец", 3_000_000),
    ]
    for name, pos, age, ovr, nationality, price in free_agents_data:
        await db.execute(
            "INSERT OR IGNORE INTO free_agents (name, position, age, ovr, nationality, asking_price) VALUES (?,?,?,?,?,?)",
            (name, pos, age, ovr, nationality, price),
        )


async def _seed_national_teams(db):
    """Seed 6 national teams."""
    national_teams = [
        ("Испания", "🇪🇸", "4-3-3"),
        ("Франция", "🇫🇷", "4-2-3-1"),
        ("Германия", "🇩🇪", "4-3-3"),
        ("Бразилия", "🇧🇷", "4-3-3"),
        ("Аргентина", "🇦🇷", "4-3-3"),
        ("Португалия", "🇵🇹", "4-4-2"),
    ]
    for country, emoji, formation in national_teams:
        await db.execute(
            "INSERT OR IGNORE INTO national_teams (country, emoji, formation) VALUES (?,?,?)",
            (country, emoji, formation),
        )


async def auto_seed_if_needed(db: aiosqlite.Connection):
    """Seed 20 La Liga clubs + players + initial game_state + free agents + national teams if empty."""
    # Ensure free agents and national teams are seeded even if clubs already exist
    async with db.execute("SELECT COUNT(*) as cnt FROM free_agents") as cur:
        fa_row = await cur.fetchone()
        if not fa_row or fa_row["cnt"] == 0:
            await _seed_free_agents(db)
            await db.commit()

    async with db.execute("SELECT COUNT(*) as cnt FROM national_teams") as cur:
        nt_row = await cur.fetchone()
        if not nt_row or nt_row["cnt"] == 0:
            await _seed_national_teams(db)
            await db.commit()

    async with db.execute("SELECT COUNT(*) as cnt FROM clubs") as cur:
        row = await cur.fetchone()
        if row and row["cnt"] > 0:
            return  # Already seeded

    logger.info("Seeding database with La Liga clubs and players…")

    # Insert clubs
    club_ids = []
    for name, short_name, budget, tier in _CLUB_SEED:
        async with db.execute(
            "INSERT INTO clubs (name, short_name, budget, season_budget, tier) VALUES (?,?,?,?,?)",
            (name, short_name, budget, budget, tier),
        ) as cur:
            club_ids.append(cur.lastrowid)

    # Insert players for each club
    for club_id, (_, _, _, tier) in zip(club_ids, _CLUB_SEED):
        players = _make_players_for_club(club_id, tier)
        for p in players:
            await db.execute(
                "INSERT INTO players (club_id, name, position, age, ovr) VALUES (?,?,?,?,?)",
                (p["club_id"], p["name"], p["position"], p["age"], p["ovr"]),
            )

    # Seed game_state
    await db.execute("INSERT OR IGNORE INTO game_state (key,value) VALUES ('season','1')")
    await db.execute("INSERT OR IGNORE INTO game_state (key,value) VALUES ('round','1')")
    await db.execute("INSERT OR IGNORE INTO game_state (key,value) VALUES ('transfer_window','open')")
    await db.execute("INSERT OR IGNORE INTO game_state (key,value) VALUES ('autopilot','off')")

    # Seed standings for season 1
    for club_id in club_ids:
        await db.execute(
            "INSERT OR IGNORE INTO standings (season, club_id) VALUES (1, ?)",
            (club_id,),
        )

    # Generate fixtures and copa bracket for season 1
    await generate_season_fixtures(db, 1)
    await generate_copa_bracket(db, 1)

    # Seed free agents
    await _seed_free_agents(db)

    # Seed national teams
    await _seed_national_teams(db)

    await db.commit()
    logger.info("Seeding complete.")


# ---------------------------------------------------------------------------
# Game state helpers
# ---------------------------------------------------------------------------

async def get_game_state(db: aiosqlite.Connection, key: str, default: str = "") -> str:
    async with db.execute("SELECT value FROM game_state WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
        return row["value"] if row else default


async def update_game_state(db: aiosqlite.Connection, key: str, value: str):
    await db.execute(
        "INSERT INTO game_state (key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    await db.commit()


async def get_current_season(db: aiosqlite.Connection) -> int:
    return int(await get_game_state(db, "season", "1"))


async def get_current_round(db: aiosqlite.Connection) -> int:
    return int(await get_game_state(db, "round", "1"))


# ---------------------------------------------------------------------------
# Club helpers
# ---------------------------------------------------------------------------

async def get_club(db: aiosqlite.Connection, club_id: int) -> Optional[dict]:
    async with db.execute("SELECT * FROM clubs WHERE id=?", (club_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_club_by_owner(db: aiosqlite.Connection, owner_id: int) -> Optional[dict]:
    async with db.execute("SELECT * FROM clubs WHERE owner_id=?", (owner_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_clubs(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute("SELECT * FROM clubs ORDER BY id") as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_available_clubs(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute("SELECT * FROM clubs WHERE owner_id IS NULL ORDER BY id") as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def update_club_budget(db: aiosqlite.Connection, club_id: int, delta: int):
    """Add delta (can be negative) to the club's budget."""
    await db.execute(
        "UPDATE clubs SET budget = budget + ? WHERE id=?",
        (delta, club_id),
    )
    await db.commit()


async def set_club_budget(db: aiosqlite.Connection, club_id: int, amount: int):
    """Set the club's budget to an exact amount."""
    await db.execute("UPDATE clubs SET budget=? WHERE id=?", (amount, club_id))
    await db.commit()


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

async def get_players(db: aiosqlite.Connection, club_id: int) -> list[dict]:
    async with db.execute(
        "SELECT * FROM players WHERE club_id=? AND is_retired=0 ORDER BY ovr DESC",
        (club_id,),
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_player(db: aiosqlite.Connection, player_id: int) -> Optional[dict]:
    async with db.execute("SELECT * FROM players WHERE id=?", (player_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_player_by_name(db: aiosqlite.Connection, name: str) -> Optional[dict]:
    """Case-insensitive fuzzy match by player name."""
    async with db.execute(
        "SELECT * FROM players WHERE LOWER(name) LIKE ? AND is_retired=0 LIMIT 1",
        (f"%{name.lower()}%",),
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Manager helpers
# ---------------------------------------------------------------------------

async def get_manager(db: aiosqlite.Connection, user_id: int) -> Optional[dict]:
    async with db.execute("SELECT * FROM managers WHERE user_id=?", (user_id,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def create_manager(db: aiosqlite.Connection, user_id: int, club_id: int, username: str = "") -> dict:
    # Update the club's owner
    await db.execute("UPDATE clubs SET owner_id=? WHERE id=?", (user_id, club_id))
    # Insert or update manager record
    await db.execute(
        """INSERT INTO managers (user_id, club_id, username) VALUES (?,?,?)
           ON CONFLICT(user_id) DO UPDATE SET club_id=excluded.club_id, username=excluded.username""",
        (user_id, club_id, username),
    )
    await db.commit()
    return await get_manager(db, user_id)


# ---------------------------------------------------------------------------
# Standings
# ---------------------------------------------------------------------------

async def get_standings(db: aiosqlite.Connection, season: int) -> list[dict]:
    async with db.execute(
        """SELECT s.*, c.name, c.short_name
           FROM standings s JOIN clubs c ON c.id=s.club_id
           WHERE s.season=?
           ORDER BY s.points DESC, (s.gf - s.ga) DESC, s.gf DESC""",
        (season,),
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def _ensure_standing(db: aiosqlite.Connection, season: int, club_id: int):
    await db.execute(
        "INSERT OR IGNORE INTO standings (season, club_id) VALUES (?,?)",
        (season, club_id),
    )


# ---------------------------------------------------------------------------
# Match helpers
# ---------------------------------------------------------------------------

async def apply_match_result(
    db: aiosqlite.Connection,
    match_id: int,
    home_goals: int,
    away_goals: int,
    scorers: list,
    yellow_cards: list,
    red_cards: list,
    injuries: list,
    narrative: str,
    possession_home: int,
    shots_home: int,
    shots_away: int,
    mvp: str,
) -> dict:
    """Persist match result and update standings / player stats."""

    # Fetch match row
    async with db.execute("SELECT * FROM matches WHERE id=?", (match_id,)) as cur:
        match = await cur.fetchone()
    if not match:
        raise ValueError(f"Match {match_id} not found")

    match = dict(match)
    season = match["season"]
    home_id = match["home_club_id"]
    away_id = match["away_club_id"]

    # Update match row
    await db.execute(
        """UPDATE matches SET
            home_goals=?, away_goals=?,
            home_possession=?, away_possession=?,
            home_shots=?, away_shots=?,
            scorers_json=?, yellow_cards_json=?, red_cards_json=?, injuries_json=?,
            mvp=?, narrative=?, status='played'
           WHERE id=?""",
        (
            home_goals, away_goals,
            possession_home, 100 - possession_home,
            shots_home, shots_away,
            json.dumps(scorers, ensure_ascii=False),
            json.dumps(yellow_cards, ensure_ascii=False),
            json.dumps(red_cards, ensure_ascii=False),
            json.dumps(injuries, ensure_ascii=False),
            mvp, narrative, match_id,
        ),
    )

    # Determine result
    if home_goals > away_goals:
        hw, hd, hl, aw, ad, al, hp, ap = 1, 0, 0, 0, 0, 1, 3, 0
    elif home_goals == away_goals:
        hw, hd, hl, aw, ad, al, hp, ap = 0, 1, 0, 0, 1, 0, 1, 1
    else:
        hw, hd, hl, aw, ad, al, hp, ap = 0, 0, 1, 1, 0, 0, 0, 3

    # Ensure standing rows
    await _ensure_standing(db, season, home_id)
    await _ensure_standing(db, season, away_id)

    # Update standings
    await db.execute(
        """UPDATE standings SET
            played=played+1, wins=wins+?, draws=draws+?, losses=losses+?,
            gf=gf+?, ga=ga+?, points=points+?
           WHERE season=? AND club_id=?""",
        (hw, hd, hl, home_goals, away_goals, hp, season, home_id),
    )
    await db.execute(
        """UPDATE standings SET
            played=played+1, wins=wins+?, draws=draws+?, losses=losses+?,
            gf=gf+?, ga=ga+?, points=points+?
           WHERE season=? AND club_id=?""",
        (aw, ad, al, away_goals, home_goals, ap, season, away_id),
    )

    # Update player stats (goals)
    for scorer in scorers:
        pname = scorer.get("name", "")
        side = scorer.get("side", "")
        club_id = home_id if side == "home" else away_id
        await db.execute(
            "UPDATE players SET goals=goals+1 WHERE LOWER(name) LIKE ? AND club_id=?",
            (f"%{pname.lower()}%", club_id),
        )

    # Yellow cards
    for yc in yellow_cards:
        pname = yc.get("name", "")
        side = yc.get("side", "")
        club_id = home_id if side == "home" else away_id
        await db.execute(
            "UPDATE players SET yellow_cards=yellow_cards+1 WHERE LOWER(name) LIKE ? AND club_id=?",
            (f"%{pname.lower()}%", club_id),
        )

    # Red cards -> 1 suspension round
    for rc in red_cards:
        pname = rc.get("name", "")
        side = rc.get("side", "")
        club_id = home_id if side == "home" else away_id
        await db.execute(
            "UPDATE players SET red_cards=red_cards+1, suspension_rounds=suspension_rounds+1 WHERE LOWER(name) LIKE ? AND club_id=?",
            (f"%{pname.lower()}%", club_id),
        )

    # Injuries
    for inj in injuries:
        pname = inj.get("name", "")
        rounds = inj.get("rounds_out", 1)
        await db.execute(
            "UPDATE players SET injury_rounds=injury_rounds+? WHERE LOWER(name) LIKE ? AND is_retired=0",
            (rounds, f"%{pname.lower()}%"),
        )

    await db.commit()
    return match


# ---------------------------------------------------------------------------
# Fixture generation
# ---------------------------------------------------------------------------

async def generate_season_fixtures(db: aiosqlite.Connection, season: int) -> int:
    """Generate a round-robin schedule for the season. Returns number of rounds."""
    async with db.execute("SELECT id FROM clubs ORDER BY id") as cur:
        rows = await cur.fetchall()
    clubs = [r["id"] for r in rows]

    if len(clubs) % 2 != 0:
        clubs.append(None)  # Bye

    n = len(clubs)
    rounds_count = (n - 1) * 2  # Home and away
    fixtures = []

    half = n // 2
    # First half of season (home/away)
    for rnd in range(1, n):
        for i in range(half):
            home = clubs[i]
            away = clubs[n - 1 - i]
            if home and away:
                fixtures.append((season, rnd, home, away))
        # Rotate (keep clubs[0] fixed)
        clubs = [clubs[0]] + [clubs[-1]] + clubs[1:-1]

    # Second half (reverse home/away)
    half_rounds = n - 1
    for i, (s, r, h, a) in enumerate(fixtures[:]):
        fixtures.append((s, r + half_rounds, a, h))

    for f in fixtures:
        await db.execute(
            "INSERT INTO matches (season, round, home_club_id, away_club_id) VALUES (?,?,?,?)",
            f,
        )

    # Init standings for all clubs
    async with db.execute("SELECT id FROM clubs") as cur:
        all_clubs = [r["id"] for r in await cur.fetchall()]
    for cid in all_clubs:
        await db.execute(
            "INSERT OR IGNORE INTO standings (season, club_id) VALUES (?,?)",
            (season, cid),
        )

    await db.commit()
    return rounds_count


async def generate_copa_bracket(db: aiosqlite.Connection, season: int) -> int:
    """Generate Copa del Rey bracket. Returns number of matches."""
    async with db.execute("SELECT id FROM clubs ORDER BY id") as cur:
        rows = await cur.fetchall()
    clubs = [r["id"] for r in rows]
    random.shuffle(clubs)

    matches = []
    pairs = [(clubs[i], clubs[i + 1]) for i in range(0, len(clubs) - 1, 2)]
    for home, away in pairs:
        matches.append((season, "Round of 16", home, away))

    for m in matches:
        await db.execute(
            "INSERT INTO copa_matches (season, round_name, home_club_id, away_club_id) VALUES (?,?,?,?)",
            m,
        )

    await db.commit()
    return len(matches)


# ---------------------------------------------------------------------------
# Season rollover
# ---------------------------------------------------------------------------

async def execute_full_season_rollover(db: aiosqlite.Connection, season: int):
    """
    - Increment season and reset round to 1
    - Age all players by 1 year; retire those 36+
    - Generate youth regens for each club
    - Reset player seasonal stats (goals, assists, cards)
    - Generate fixtures for new season
    - Seed standings for new season
    """
    new_season = season + 1

    # Age players
    await db.execute("UPDATE players SET age=age+1 WHERE is_retired=0")
    # Retire players 36+
    await db.execute("UPDATE players SET is_retired=1 WHERE age>=36 AND is_retired=0")

    # Generate regens (1-2 young players per club to replace retirees)
    async with db.execute("SELECT id, tier FROM clubs") as cur:
        clubs = await cur.fetchall()

    for club_row in clubs:
        club_id = club_row["id"]
        tier = club_row["tier"]
        # Count active players
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM players WHERE club_id=? AND is_retired=0",
            (club_id,),
        ) as cur:
            cnt_row = await cur.fetchone()
        active = cnt_row["cnt"] if cnt_row else 20
        regens_needed = max(0, 20 - active)

        for i in range(regens_needed):
            lo, hi = _get_ovr_range(tier, "CM")
            regen_ovr = random.randint(lo - 10, lo + 2)
            regen_ovr = max(55, regen_ovr)
            pos = random.choice(_POSITIONS)
            name = random.choice(_SPANISH_NAMES) + f" Jr.{random.randint(1, 99)}"
            await db.execute(
                "INSERT INTO players (club_id, name, position, age, ovr) VALUES (?,?,?,?,?)",
                (club_id, name, pos, random.randint(17, 19), regen_ovr),
            )

    # Reset seasonal player stats
    await db.execute(
        "UPDATE players SET goals=0, assists=0, yellow_cards=0, red_cards=0 WHERE is_retired=0"
    )
    # Tick down injury/suspension
    await db.execute(
        "UPDATE players SET injury_rounds=MAX(0, injury_rounds-1) WHERE injury_rounds>0"
    )
    await db.execute(
        "UPDATE players SET suspension_rounds=MAX(0, suspension_rounds-1) WHERE suspension_rounds>0"
    )

    # Update game state
    await update_game_state(db, "season", str(new_season))
    await update_game_state(db, "round", "1")

    # Generate new fixtures
    await generate_season_fixtures(db, new_season)
    await generate_copa_bracket(db, new_season)

    await db.commit()
    logger.info(f"Season rollover complete: season {season} -> {new_season}")


# ---------------------------------------------------------------------------
# Reset Season (full reset to Season 1 or current season wipe)
# ---------------------------------------------------------------------------

async def reset_current_season(db: aiosqlite.Connection, reset_to_season_1: bool = True):
    """
    Completely reset the season:
    - If reset_to_season_1=True: resets season counter back to 1, round to 1.
    - Wipes matches, standings, and copa_matches for the current or all seasons.
    - Cleans up transfers, injuries, suspensions, goals, assists and cards.
    - Regenerates double round-robin fixtures and standings for Season 1.
    """
    target_season = 1 if reset_to_season_1 else int(await get_game_state(db, "season", "1"))

    # Reset game state
    await update_game_state(db, "season", str(target_season))
    await update_game_state(db, "round", "1")
    await update_game_state(db, "transfer_window", "open")
    await update_game_state(db, "autopilot", "off")

    # Reset players seasonal stats and statuses
    await db.execute("""
        UPDATE players SET
            goals = 0,
            assists = 0,
            yellow_cards = 0,
            red_cards = 0,
            injury_rounds = 0,
            suspension_rounds = 0
    """)

    # Reset clubs budgets to initial starting budgets
    for _, short_name, budget, _ in _CLUB_SEED:
        await db.execute(
            "UPDATE clubs SET budget = ?, season_budget = ? WHERE short_name = ?",
            (budget, budget, short_name),
        )

    # Delete existing matches and standings
    await db.execute("DELETE FROM matches")
    await db.execute("DELETE FROM copa_matches")
    await db.execute("DELETE FROM standings")
    await db.execute("DELETE FROM training_log")
    await db.execute("DELETE FROM transfers")

    # Regenerate fixtures & standings for the target season
    await generate_season_fixtures(db, target_season)
    await generate_copa_bracket(db, target_season)

    await db.commit()
    logger.info(f"Season reset completed! Reset to season {target_season}, round 1.")


# ---------------------------------------------------------------------------
# Free Agent helpers
# ---------------------------------------------------------------------------

async def get_free_agents(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute(
        "SELECT * FROM free_agents ORDER BY ovr DESC"
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_free_agent_by_name(db: aiosqlite.Connection, name: str) -> Optional[dict]:
    async with db.execute(
        "SELECT * FROM free_agents WHERE LOWER(name) LIKE ? LIMIT 1",
        (f"%{name.lower()}%",),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def delete_free_agent(db: aiosqlite.Connection, agent_id: int):
    await db.execute("DELETE FROM free_agents WHERE id=?", (agent_id,))
    await db.commit()


async def add_free_agent(db: aiosqlite.Connection, name: str, position: str,
                         age: int, ovr: int, nationality: str, asking_price: int) -> int:
    async with db.execute(
        "INSERT INTO free_agents (name, position, age, ovr, nationality, asking_price) VALUES (?,?,?,?,?,?)",
        (name, position, age, ovr, nationality, asking_price),
    ) as cur:
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Sponsor offer helpers
# ---------------------------------------------------------------------------

_SPONSOR_TIERS = {
    "bronze":   ("Localia TV",        (15, 20), 500_000,    50_000),
    "silver":   ("Bet365 España",     (10, 15), 2_000_000,  200_000),
    "gold":     ("Santander Bank",    (5,  10), 6_000_000,  500_000),
    "platinum": ("Emirates Airlines", (1,   5), 15_000_000, 1_000_000),
}


async def generate_sponsor_offers(db: aiosqlite.Connection, club_id: int):
    """Generate 3 sponsor offers for a club."""
    # Clear old unaccepted offers for this club
    await db.execute(
        "DELETE FROM sponsor_offers WHERE club_id=? AND is_accepted=0",
        (club_id,),
    )
    tiers = ["bronze", "silver", "gold", "platinum"]
    # Pick 3 random tiers
    import random as _random
    chosen = _random.sample(tiers, 3)
    for tier in chosen:
        sponsor_name, (pos_min, pos_max), bonus, weekly = _SPONSOR_TIERS[tier]
        target_pos = _random.randint(pos_min, pos_max)
        await db.execute(
            """INSERT INTO sponsor_offers (club_id, sponsor_name, tier, target_position, bonus_amount, weekly_income)
               VALUES (?,?,?,?,?,?)""",
            (club_id, sponsor_name, tier, target_pos, bonus, weekly),
        )
    await db.commit()
    return await get_pending_offers(db, club_id)


async def get_pending_offers(db: aiosqlite.Connection, club_id: int) -> list[dict]:
    async with db.execute(
        "SELECT * FROM sponsor_offers WHERE club_id=? AND is_accepted=0 ORDER BY bonus_amount DESC",
        (club_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def accept_sponsor_offer(db: aiosqlite.Connection, offer_id: int, club_id: int):
    """Accept a sponsor offer: mark it, create contract, add weekly_income * 10 to budget."""
    async with db.execute(
        "SELECT * FROM sponsor_offers WHERE id=? AND club_id=?",
        (offer_id, club_id),
    ) as cur:
        offer = await cur.fetchone()
    if not offer:
        return False
    offer = dict(offer)

    # Mark as accepted
    await db.execute("UPDATE sponsor_offers SET is_accepted=1 WHERE id=?", (offer_id,))

    # Create/update sponsor contract
    await db.execute(
        """INSERT INTO sponsor_contracts (club_id, sponsor_name, target_position, bonus_amount, risk_level)
           VALUES (?,?,?,?,'medium')
           ON CONFLICT(club_id) DO UPDATE SET
             sponsor_name=excluded.sponsor_name,
             target_position=excluded.target_position,
             bonus_amount=excluded.bonus_amount""",
        (club_id, offer["sponsor_name"], offer["target_position"], offer["bonus_amount"]),
    )

    # Add immediate payment (weekly_income * 10)
    immediate = offer["weekly_income"] * 10
    if immediate > 0:
        await db.execute(
            "UPDATE clubs SET budget=budget+? WHERE id=?",
            (immediate, club_id),
        )

    await db.commit()
    return True


async def get_sponsor_contract(db: aiosqlite.Connection, club_id: int) -> Optional[dict]:
    async with db.execute(
        "SELECT * FROM sponsor_contracts WHERE club_id=?",
        (club_id,),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# National team helpers
# ---------------------------------------------------------------------------

async def get_national_teams(db: aiosqlite.Connection) -> list[dict]:
    async with db.execute("SELECT * FROM national_teams ORDER BY id") as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_national_team_by_manager(db: aiosqlite.Connection, user_id: int) -> Optional[dict]:
    async with db.execute(
        "SELECT * FROM national_teams WHERE manager_user_id=?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_national_team_by_id(db: aiosqlite.Connection, team_id: int) -> Optional[dict]:
    async with db.execute(
        "SELECT * FROM national_teams WHERE id=?", (team_id,)
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def assign_national_team_manager(db: aiosqlite.Connection, team_id: int, user_id: int):
    await db.execute(
        "UPDATE national_teams SET manager_user_id=? WHERE id=?",
        (user_id, team_id),
    )
    await db.commit()


async def get_national_squad(db: aiosqlite.Connection, team_id: int) -> list[dict]:
    async with db.execute(
        """SELECT p.*, c.name as club_name, nts.is_called_up
           FROM national_team_squads nts
           JOIN players p ON p.id = nts.player_id
           JOIN clubs c ON c.id = p.club_id
           WHERE nts.national_team_id=? AND nts.is_called_up=1
           ORDER BY p.ovr DESC""",
        (team_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def call_up_player(db: aiosqlite.Connection, team_id: int, player_id: int):
    await db.execute(
        """INSERT INTO national_team_squads (national_team_id, player_id, is_called_up)
           VALUES (?,?,1)
           ON CONFLICT(national_team_id, player_id) DO UPDATE SET is_called_up=1""",
        (team_id, player_id),
    )
    await db.commit()


async def release_player_from_national_team(db: aiosqlite.Connection, team_id: int, player_id: int):
    await db.execute(
        "UPDATE national_team_squads SET is_called_up=0 WHERE national_team_id=? AND player_id=?",
        (team_id, player_id),
    )
    await db.commit()


async def start_international_break(db: aiosqlite.Connection, season: int):
    """Set intl_break=on and create international match fixtures."""
    await update_game_state(db, "intl_break", "on")
    # Get all national teams
    async with db.execute("SELECT id FROM national_teams ORDER BY id") as cur:
        teams = [r["id"] for r in await cur.fetchall()]
    # Create round-robin pairs
    if len(teams) >= 2:
        import random as _random
        _random.shuffle(teams)
        pairs = [(teams[i], teams[i+1]) for i in range(0, len(teams)-1, 2)]
        for home_id, away_id in pairs:
            await db.execute(
                """INSERT INTO international_matches (season, home_team_id, away_team_id)
                   VALUES (?,?,?)""",
                (season, home_id, away_id),
            )
        await db.commit()
        return len(pairs)
    await db.commit()
    return 0


async def end_international_break(db: aiosqlite.Connection):
    await update_game_state(db, "intl_break", "off")
    await db.commit()
