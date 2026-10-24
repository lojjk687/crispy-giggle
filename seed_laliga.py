"""
seed_laliga.py - Populate the La Liga AI Dynasty database with 20 clubs
and their squads.  Run once before starting the bot:

    python seed_laliga.py

The script is idempotent: it skips seeding if clubs already exist.
"""

from __future__ import annotations

import asyncio
import random
import sys

import aiosqlite

from database import DB_PATH, init_db, update_game_state

# ---------------------------------------------------------------------------
# Club definitions
# ---------------------------------------------------------------------------

CLUBS: list[dict] = [
    # Tier 1 – Top clubs
    {"name": "Real Madrid",    "short_name": "RMA", "tier": 1, "budget": 130_000_000.0},
    {"name": "Barcelona",      "short_name": "BAR", "tier": 1, "budget": 125_000_000.0},
    {"name": "Atletico Madrid","short_name": "ATM", "tier": 1, "budget":  85_000_000.0},
    {"name": "Sevilla",        "short_name": "SEV", "tier": 1, "budget":  65_000_000.0},
    # Tier 2 – Mid clubs
    {"name": "Valencia",       "short_name": "VAL", "tier": 2, "budget":  50_000_000.0},
    {"name": "Real Betis",     "short_name": "BET", "tier": 2, "budget":  48_000_000.0},
    {"name": "Villarreal",     "short_name": "VIL", "tier": 2, "budget":  52_000_000.0},
    {"name": "Athletic Club",  "short_name": "ATH", "tier": 2, "budget":  46_000_000.0},
    {"name": "Real Sociedad",  "short_name": "RSO", "tier": 2, "budget":  48_000_000.0},
    {"name": "Osasuna",        "short_name": "OSA", "tier": 2, "budget":  38_000_000.0},
    {"name": "Celta Vigo",     "short_name": "CEL", "tier": 2, "budget":  35_000_000.0},
    {"name": "Getafe",         "short_name": "GET", "tier": 2, "budget":  33_000_000.0},
    {"name": "Rayo Vallecano", "short_name": "RAY", "tier": 2, "budget":  30_000_000.0},
    {"name": "Mallorca",       "short_name": "MAL", "tier": 2, "budget":  32_000_000.0},
    # Tier 3 – Underdogs
    {"name": "Cadiz",          "short_name": "CAD", "tier": 3, "budget":  25_000_000.0},
    {"name": "Almeria",        "short_name": "ALM", "tier": 3, "budget":  22_000_000.0},
    {"name": "Granada",        "short_name": "GRA", "tier": 3, "budget":  23_000_000.0},
    {"name": "Las Palmas",     "short_name": "LPA", "tier": 3, "budget":  24_000_000.0},
    {"name": "Girona",         "short_name": "GIR", "tier": 3, "budget":  28_000_000.0},
    {"name": "Leganes",        "short_name": "LEG", "tier": 3, "budget":  22_000_000.0},
]

# ---------------------------------------------------------------------------
# Name pools – fictional Spanish/international style names
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    # Spanish
    "Alejandro", "Andrés", "Antonio", "Carlos", "Daniel", "David", "Diego",
    "Eduardo", "Emilio", "Enrique", "Fernando", "Francisco", "Gabriel",
    "Gonzalo", "Hugo", "Iván", "Javier", "Jorge", "José", "Juan", "Julio",
    "Luis", "Manuel", "Marcos", "Mario", "Miguel", "Pablo", "Pedro",
    "Rafael", "Raúl", "Roberto", "Rodrigo", "Rubén", "Sergio", "Vicente",
    "Álvaro", "Borja", "Isco", "Kiko", "Nacho",
    # Brazilian-flavoured (common in La Liga)
    "Thiago", "Lucas", "Felipe", "Bruno", "Matheus", "Gabriel",
    # Other international
    "Iker", "Mikel", "Asier", "Unai", "Aritz", "Aitor", "Gaizka",
    "Kepa", "Yeray", "Oihan", "Dani", "Xabi", "Joseba",
    # African-origin (common signings)
    "Oumar", "Ibrahima", "Moussa", "Cheikh", "Mamadou", "Ismaël",
]

LAST_NAMES = [
    "Alonso", "Álvarez", "Blanco", "Castillo", "Castro", "Cruz", "Díaz",
    "Domínguez", "Fernández", "García", "Gómez", "González", "Herrera",
    "Jiménez", "López", "Martín", "Martínez", "Moreno", "Muñoz", "Navarro",
    "Ortega", "Pérez", "Ramírez", "Reyes", "Rodríguez", "Romero", "Ruiz",
    "Sánchez", "Santos", "Serrano", "Torres", "Vázquez", "Vega", "Velázquez",
    "Ramos", "Piqué", "Busquets", "Pedri", "Gavi", "Olmo", "Yamal",
    "Bellingham", "Vinicius", "Camavinga", "Valverde", "Kroos", "Modric",
    "Suárez", "Cavani", "Griezmann", "Costa", "Morata", "Aspas",
    "Iago", "Banega", "Rakitic", "Canales", "Merino", "Silva",
    "Dia", "En-Nesyri", "Ocampos", "Navas", "Acuña",
    "Cardona", "Arana", "Budimir", "Abde", "Guridi",
]

# Squad template: (position, count)
# 2 GK, 4 CB, 2 LB, 2 RB, 2 CDM, 2 CM, 2 CAM, 2 LW, 2 RW, 2 ST → 22 total but we aim ~18
# Adjusted to 18 per club

POSITION_SLOTS: list[tuple[str, int]] = [
    ("GK",  2),
    ("CB",  3),
    ("LB",  1),
    ("RB",  1),
    ("CDM", 2),
    ("CM",  2),
    ("CAM", 1),
    ("LW",  2),
    ("RW",  2),
    ("ST",  2),
]  # total = 18

# OVR bands per tier
OVR_BANDS: dict[int, tuple[int, int]] = {
    1: (75, 88),
    2: (68, 78),
    3: (62, 72),
}

# Age spread per position role
AGE_BAND: dict[str, tuple[int, int]] = {
    "GK":  (20, 34),
    "CB":  (19, 32),
    "LB":  (19, 31),
    "RB":  (19, 31),
    "CDM": (20, 32),
    "CM":  (18, 31),
    "CAM": (18, 30),
    "LW":  (18, 30),
    "RW":  (18, 30),
    "ST":  (19, 32),
}


def _random_name(used: set[str]) -> str:
    for _ in range(100):
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        if name not in used:
            used.add(name)
            return name
    # Fallback with suffix to guarantee uniqueness
    suffix = random.randint(10, 99)
    name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)} {suffix}"
    used.add(name)
    return name


def _generate_squad(tier: int, used_names: set[str]) -> list[dict]:
    ovr_min, ovr_max = OVR_BANDS[tier]
    players: list[dict] = []

    for position, count in POSITION_SLOTS:
        age_min, age_max = AGE_BAND[position]
        for _ in range(count):
            # Slight OVR variance per position within band
            pos_boost = 2 if position in ("ST", "GK") else 0
            ovr = random.randint(ovr_min, min(ovr_max + pos_boost, 99))
            age = random.randint(age_min, age_max)
            name = _random_name(used_names)
            players.append({"name": name, "position": position, "age": age, "ovr": ovr})

    return players


# ---------------------------------------------------------------------------
# Main seed routine
# ---------------------------------------------------------------------------


async def seed(db: aiosqlite.Connection) -> int:
    """Insert clubs and players.  Returns total players inserted."""

    # Check if already seeded
    async with db.execute("SELECT COUNT(*) as c FROM clubs") as cur:
        row = await cur.fetchone()
    if row and row["c"] > 0:
        print("[WARN] Database already contains clubs - skipping seed.")
        return 0

    used_names: set[str] = set()
    total_players = 0

    for i, club in enumerate(CLUBS, start=1):
        print(f"  [{i:02d}/20] Seeding {club['name']} (Tier {club['tier']}, "
              f"budget EUR{club['budget'] / 1_000_000:.0f}M) ...")

        # Insert club
        async with db.execute(
            """
            INSERT INTO clubs (name, short_name, tier, budget, season,
                               copa_eliminated, sponsor_contract, sponsor_bonus)
            VALUES (?, ?, ?, ?, 1, 0, 'safe', 0.0)
            """,
            (club["name"], club["short_name"], club["tier"], club["budget"]),
        ) as cur:
            club_id = cur.lastrowid

        # Generate & insert squad
        squad = _generate_squad(club["tier"], used_names)
        for p in squad:
            await db.execute(
                """
                INSERT INTO players
                    (club_id, name, position, age, ovr, season_joined)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (club_id, p["name"], p["position"], p["age"], p["ovr"]),
            )
            total_players += 1

        # Insert standings row for season 1
        await db.execute(
            "INSERT OR IGNORE INTO standings (season, club_id) VALUES (1, ?)",
            (club_id,),
        )

    await db.commit()
    return total_players


async def main() -> None:
    print("=" * 60)
    print("  La Liga AI Dynasty - Database Seeder")
    print("=" * 60)

    print("\n[INFO] Initialising database schema ...")
    await init_db()
    print("       Schema ready.")

    print("\n[INFO] Seeding clubs and squads ...")
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys = ON")

        total_players = await seed(db)

        if total_players > 0:
            # Initialise game state
            await update_game_state(db, "current_season", 1)
            await update_game_state(db, "current_round", 1)
            await db.commit()

    if total_players > 0:
        print(f"\n[OK] Seeding complete!")
        print(f"     Clubs   : {len(CLUBS)}")
        print(f"     Players : {total_players}")
        print(f"     DB path : {DB_PATH}")
    else:
        print("\n[SKIP] No new data inserted (database was already populated).")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
