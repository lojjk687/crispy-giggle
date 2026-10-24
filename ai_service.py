"""
ai_service.py — La Liga AI Dynasty
All Gemini API interactions strictly via google-genai SDK (gemini-2.5-flash).
Includes free-tier rate-limiting, exponential backoff (up to 4 attempts, 5-20s delays),
and resilient fallbacks so the autonomous engine never crashes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any, List, Optional

try:
    import google.genai as genai
    from google.genai import types
except ImportError:
    genai = None
    types = None
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — Strictly locked to gemini-2.5-flash
# ---------------------------------------------------------------------------
MODEL_ID: str = "gemini-2.5-flash"
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

_client: Optional[genai.Client] = None

def get_client() -> Optional[genai.Client]:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY", "")
        if key:
            _client = genai.Client(api_key=key)
    return _client


# ---------------------------------------------------------------------------
# Rate Limiter & Backoff (Free-Tier Protection)
# ---------------------------------------------------------------------------
class _RateLimiter:
    """Async token/interval rate limiter to respect free-tier RPM (15 RPM)."""
    def __init__(self, min_interval_seconds: float = 3.5):
        self._min_interval = min_interval_seconds
        self._lock = asyncio.Lock()
        self._last_time: float = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            elapsed = now - self._last_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_time = loop.time()

_rate_limiter = _RateLimiter(min_interval_seconds=3.5)


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class GoalEvent(BaseModel):
    player_name: str
    minute: int
    is_penalty: bool = False
    is_own_goal: bool = False


class CardEvent(BaseModel):
    player_name: str
    minute: int


class InjuryEvent(BaseModel):
    player_name: str
    rounds_out: int  # 1-3


class MatchSimulationResult(BaseModel):
    home_goals: int
    away_goals: int
    scorers_home: List[GoalEvent] = Field(default_factory=list)
    scorers_away: List[GoalEvent] = Field(default_factory=list)
    possession_home: int = 50  # 0-100
    shots_on_target_home: int = 4
    shots_on_target_away: int = 4
    yellow_cards_home: List[CardEvent] = Field(default_factory=list)
    yellow_cards_away: List[CardEvent] = Field(default_factory=list)
    red_cards_home: List[CardEvent] = Field(default_factory=list)
    red_cards_away: List[CardEvent] = Field(default_factory=list)
    injuries_home: List[InjuryEvent] = Field(default_factory=list)
    injuries_away: List[InjuryEvent] = Field(default_factory=list)
    mvp: str = "Игрок матча"
    narrative_report: str = "Напряжённый матч Ла Лиги."

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, item: str) -> Any:
        try:
            return getattr(self, item)
        except AttributeError:
            raise KeyError(item)

    @property
    def scorers(self) -> List[dict]:
        res: List[dict] = []
        for s in (self.scorers_home or []):
            d = s.model_dump() if hasattr(s, "model_dump") else (dict(s) if isinstance(s, dict) else {"name": str(s)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "home"
            d["club"] = "home"
            res.append(d)
        for s in (self.scorers_away or []):
            d = s.model_dump() if hasattr(s, "model_dump") else (dict(s) if isinstance(s, dict) else {"name": str(s)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "away"
            d["club"] = "away"
            res.append(d)
        return res

    @property
    def yellow_cards(self) -> List[dict]:
        res: List[dict] = []
        for c in (self.yellow_cards_home or []):
            d = c.model_dump() if hasattr(c, "model_dump") else (dict(c) if isinstance(c, dict) else {"name": str(c)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "home"
            res.append(d)
        for c in (self.yellow_cards_away or []):
            d = c.model_dump() if hasattr(c, "model_dump") else (dict(c) if isinstance(c, dict) else {"name": str(c)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "away"
            res.append(d)
        return res

    @property
    def red_cards(self) -> List[dict]:
        res: List[dict] = []
        for c in (self.red_cards_home or []):
            d = c.model_dump() if hasattr(c, "model_dump") else (dict(c) if isinstance(c, dict) else {"name": str(c)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "home"
            res.append(d)
        for c in (self.red_cards_away or []):
            d = c.model_dump() if hasattr(c, "model_dump") else (dict(c) if isinstance(c, dict) else {"name": str(c)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "away"
            res.append(d)
        return res

    @property
    def injuries(self) -> List[dict]:
        res: List[dict] = []
        for i in (self.injuries_home or []):
            d = i.model_dump() if hasattr(i, "model_dump") else (dict(i) if isinstance(i, dict) else {"name": str(i)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "home"
            res.append(d)
        for i in (self.injuries_away or []):
            d = i.model_dump() if hasattr(i, "model_dump") else (dict(i) if isinstance(i, dict) else {"name": str(i)})
            if "name" not in d and "player_name" in d:
                d["name"] = d["player_name"]
            d["side"] = "away"
            res.append(d)
        return res

    @property
    def narrative(self) -> str:
        return self.narrative_report

    @property
    def shots_home(self) -> int:
        return self.shots_on_target_home

    @property
    def shots_away(self) -> int:
        return self.shots_on_target_away


class BatchMatchSimulationResult(BaseModel):
    matches: List[MatchSimulationResult] = Field(default_factory=list)


class PlayerValuation(BaseModel):
    player_name: str
    position: str
    age: int
    ovr: int  # estimated 50-99
    market_value_eur: float
    markup_value_eur: float  # market_value * 1.20
    valuation_reasoning: str  # Russian explanation


class TransferNews(BaseModel):
    headline: str  # Fabrizio Romano style Russian headline
    body: str  # Full dramatic Russian post with emojis


class TrainingReport(BaseModel):
    focus: str
    report_text: str  # Russian coach report
    player_improvements: List[dict] = Field(default_factory=list)
    injury_risk_triggered: bool = False
    injured_player: Optional[str] = None


class AwardsCeremony(BaseModel):
    ceremony_intro: str
    ballon_dor_winner: str
    ballon_dor_text: str
    golden_boy_winner: str
    golden_boy_text: str
    best_goalkeeper_winner: str
    best_goalkeeper_text: str
    best_team: str
    best_team_text: str
    golden_boot_winner: str
    golden_boot_goals: int
    flop_player: str
    flop_text: str
    breakthrough_club: str
    breakthrough_text: str
    bus_master_club: str
    bus_master_text: str
    comeback_king_club: str
    comeback_king_text: str
    rebuilding_grant_club: str
    rebuilding_grant_text: str
    closing_speech: str


class ClubBudgetEvaluation(BaseModel):
    club_name: str
    remaining_balance: float
    ffp_tax: float = 0.0
    placement_prize: float = 0.0
    tv_rights_share: float = 0.0
    trophy_and_awards_bonus: float = 0.0
    sponsor_status: str = "Стабильный контракт"
    sponsor_contract_eur: float = 20_000_000.0
    new_season_budget: float = 40_000_000.0
    executive_summary: str = "Устойчивое финансовое положение."


class SeasonBudgetReport(BaseModel):
    season_completed: int
    new_season: int
    financial_overview: str
    club_evaluations: List[ClubBudgetEvaluation] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resilient Retry Wrapper
# ---------------------------------------------------------------------------

async def _generate_with_retry(
    prompt: str,
    response_schema: Any,
    system_instruction: Optional[str] = None,
    max_attempts: int = 4,
) -> Any:
    """
    Call Gemini client with strict rate-limiting and exponential backoff.
    Catches HTTP 429, ResourceExhausted, timeouts, and network drops.
    Retries up to 4 times with delays of 5s, 10s, 15s, 20s.
    """
    client = get_client()
    if not client:
        raise RuntimeError("GEMINI_API_KEY is not set or client unavailable.")

    delays = [5, 10, 15, 20]
    last_exc: Optional[Exception] = None

    for attempt in range(max_attempts):
        await _rate_limiter.acquire()
        try:
            kwargs: dict[str, Any] = {
                "model": MODEL_ID,
                "contents": prompt,
                "config": types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                    **({"system_instruction": system_instruction} if system_instruction else {}),
                ),
            }
            response = client.models.generate_content(**kwargs)
            return response
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            exc_str = str(exc).lower()
            is_transient = any(
                w in exc_str for w in ("429", "quota", "resourceexhausted", "rate", "timeout", "connection", "reset", "503", "500")
            )
            if attempt < max_attempts - 1 and is_transient:
                delay = delays[attempt]
                logger.warning(
                    "Gemini API rate limit/transient error (attempt %d/%d). Sleeping %ds... (%s)",
                    attempt + 1,
                    max_attempts,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("Gemini API call permanently failed: %s", exc)
                raise

    raise last_exc or RuntimeError("Unknown Gemini API error")


def _parse_json_response(text: str) -> Any:
    """Strip markdown code fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


# ---------------------------------------------------------------------------
# Fallback Generators (Zero-Crash Guarantee)
# ---------------------------------------------------------------------------

def _fallback_match(home_team: dict, away_team: dict, fast_mode: bool) -> MatchSimulationResult:
    """Deterministic, realistic match generation when Gemini is unreachable."""
    h_ovr = float(home_team.get("avg_ovr", 72))
    a_ovr = float(away_team.get("avg_ovr", 72))
    diff = h_ovr - a_ovr + 2.0  # +2 home advantage

    # Goals distribution
    base_h = max(0, min(5, int(random.gauss(1.5 + diff * 0.1, 0.9))))
    base_a = max(0, min(5, int(random.gauss(1.1 - diff * 0.1, 0.9))))

    h_players = home_team.get("players", [])
    a_players = away_team.get("players", [])

    scorers_h = []
    for _ in range(base_h):
        p = random.choice(h_players)["name"] if h_players else "Нападающий Хозяев"
        scorers_h.append(GoalEvent(player_name=p, minute=random.randint(5, 89)))

    scorers_a = []
    for _ in range(base_a):
        p = random.choice(a_players)["name"] if a_players else "Нападающий Гостей"
        scorers_a.append(GoalEvent(player_name=p, minute=random.randint(5, 89)))

    mvp = scorers_h[0].player_name if scorers_h else (scorers_a[0].player_name if scorers_a else (h_players[0]["name"] if h_players else "Голкипер"))

    narrative = (
        f"Интенсивный поединок между {home_team.get('name')} и {away_team.get('name')} завершился со счётом {base_h}:{base_a}."
        if fast_mode else
        f"Матч между {home_team.get('name')} и {away_team.get('name')} подарил зрителям яркий футбол. "
        f"Хозяева поля реализовали свои моменты ({base_h} голов), в то время как гости ответили {base_a} мячами. "
        f"Лучшим игроком встречи признан {mvp}."
    )

    return MatchSimulationResult(
        home_goals=base_h,
        away_goals=base_a,
        scorers_home=scorers_h,
        scorers_away=scorers_a,
        possession_home=min(70, max(30, int(50 + diff * 2))),
        shots_on_target_home=base_h + random.randint(2, 5),
        shots_on_target_away=base_a + random.randint(2, 4),
        yellow_cards_home=[],
        yellow_cards_away=[],
        red_cards_home=[],
        red_cards_away=[],
        injuries_home=[],
        injuries_away=[],
        mvp=mvp,
        narrative_report=narrative,
    )


# ---------------------------------------------------------------------------
# 1. simulate_match
# ---------------------------------------------------------------------------

async def simulate_match(
    home_team: dict,
    away_team: dict,
    fast_mode: bool = False,
) -> MatchSimulationResult:
    """Simulate a single match with Gemini 2.5 Flash and fallback protection."""
    narrative_instruction = (
        "Напиши КРАТКОЕ резюме матча в 2-3 предложениях на русском языке."
        if fast_mode else
        "Напиши ДРАМАТИЧЕСКИЙ эмоциональный комментарий матча на русском языке (150-200 слов) "
        "с тактическим анализом, описанием ключевых моментов и драматизма."
    )

    def _fmt_players(players: list) -> str:
        return "\n".join(
            f"  - {p['name']} ({p.get('position', '?')}, OVR: {p.get('ovr', '?')})"
            for p in players
        )

    prompt = f"""
Ты симулируешь реальный футбольный матч испанской Ла Лиги.
Модель: strictly gemini-2.5-flash.

ХОЗЯЕВА: {home_team.get('name')} (OVR: {home_team.get('avg_ovr')}, Тактика: {home_team.get('tactic')}, Капитан: {home_team.get('captain')})
Игроки:
{_fmt_players(home_team.get('players', []))}

ГОСТИ: {away_team.get('name')} (OVR: {away_team.get('avg_ovr')}, Тактика: {away_team.get('tactic')}, Капитан: {away_team.get('captain')})
Игроки:
{_fmt_players(away_team.get('players', []))}

ПРАВИЛА:
1. Голы забивают только доступные игроки.
2. Минуты от 1 до 90+.
3. {narrative_instruction}
4. MVP — ярчайший игрок матча.
5. Possession_home + away = 100.
Верни результат строго по схеме.
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=MatchSimulationResult,
            system_instruction="Ты профессиональный комментатор Ла Лиги.",
        )
        data = _parse_json_response(response.text)
        return MatchSimulationResult(**data)
    except Exception as exc:
        logger.warning("Gemini match simulation fallback triggered: %s", exc)
        return _fallback_match(home_team, away_team, fast_mode)


# ---------------------------------------------------------------------------
# 2. simulate_multiple_matches (Batched AI-vs-AI)
# ---------------------------------------------------------------------------

async def simulate_multiple_matches(matches: List[dict]) -> List[MatchSimulationResult]:
    """Batch simulate AI-vs-AI matches in a single call to save free-tier quota."""
    if not matches:
        return []

    match_blocks = []
    for i, m in enumerate(matches, start=1):
        ht = m["home_team"]
        at = m["away_team"]
        block = (
            f"Матч {i}: {ht.get('name')} (OVR {ht.get('avg_ovr')}) vs {at.get('name')} (OVR {at.get('avg_ovr')})"
        )
        match_blocks.append(block)

    prompt = f"""
Ты симулируешь тур из {len(matches)} матчей Ла Лиги.
{chr(10).join(match_blocks)}

Верни JSON-массив из {len(matches)} объектов MatchSimulationResult в том же порядке.
Краткий narrative_report (2 предложения на русском) для каждого матча.
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=BatchMatchSimulationResult,
            system_instruction="Ты спортивный симулятор Ла Лиги.",
        )
        data = _parse_json_response(response.text)
        if isinstance(data, dict) and "matches" in data and isinstance(data["matches"], list):
            return [MatchSimulationResult(**item) if isinstance(item, dict) else item for item in data["matches"]]
        if isinstance(data, list):
            return [MatchSimulationResult(**item) if isinstance(item, dict) else item for item in data]
        for k in ("matches", "results", "data"):
            if isinstance(data, dict) and k in data and isinstance(data[k], list):
                return [MatchSimulationResult(**item) if isinstance(item, dict) else item for item in data[k]]
    except Exception as exc:
        logger.warning("Batch simulation fallback triggered: %s", exc)

    return [_fallback_match(m["home_team"], m["away_team"], fast_mode=True) for m in matches]


# ---------------------------------------------------------------------------
# 3. valuate_external_player
# ---------------------------------------------------------------------------

async def valuate_external_player(player_name: str, context: str = "") -> PlayerValuation:
    """Estimate Transfermarkt valuation for external player with 20% markup."""
    prompt = f"""
Оцени игрока как эксперт Transfermarkt:
Имя: {player_name}
Контекст: {context}

Верни JSON со полями:
- position (GK, CB, LB, RB, CDM, CM, CAM, LW, RW, ST)
- age (целое число)
- ovr (50-99)
- market_value_eur (в евро)
- markup_value_eur (рыночная стоимость * 1.20)
- valuation_reasoning (обоснование на русском)
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=PlayerValuation,
            system_instruction="Ты эксперт Transfermarkt по оценке игроков.",
        )
        data = _parse_json_response(response.text)
        return PlayerValuation(**data)
    except Exception as exc:
        logger.warning("Valuation fallback triggered: %s", exc)
        base_val = random.randint(20_000_000, 60_000_000)
        return PlayerValuation(
            player_name=player_name,
            position="ST",
            age=random.randint(20, 28),
            ovr=random.randint(76, 85),
            market_value_eur=float(base_val),
            markup_value_eur=float(base_val * 1.20),
            valuation_reasoning=f"Автоматическая экспертная оценка Transfermarkt для {player_name}.",
        )


# ---------------------------------------------------------------------------
# 4. generate_transfer_news (Fabrizio Romano Mode)
# ---------------------------------------------------------------------------

async def generate_transfer_news(
    player_name: str,
    from_club: str,
    to_club: str,
    fee: float,
    is_rumor: bool = False,
) -> TransferNews:
    """Generate energetic Fabrizio Romano breaking news post."""
    fee_m = fee / 1_000_000
    prompt = f"""
Напиши пост Фабрицио Романо на русском:
Событие: {'Слух' if is_rumor else 'Официальный трансфер'}
Игрок: {player_name}
Из: {from_club} в {to_club}
Сумма: {fee_m:.1f}M€
Обязательно: эмодзи 🚨 🤝 ✅ 💰, фраза {'Переговоры идут' if is_rumor else 'ДОГОВОРИЛИСЬ! Here we go!'}
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=TransferNews,
            system_instruction="Ты Фабрицио Романо. Пишешь энергично с эмодзи.",
        )
        data = _parse_json_response(response.text)
        return TransferNews(**data)
    except Exception as exc:
        logger.warning("Transfer news fallback triggered: %s", exc)
        tag = "🚨 BREAKING" if not is_rumor else "👀 СЛУХИ"
        text = (
            f"🚨 <b>Here we go! ДОГОВОРИЛИСЬ!</b>\n\n"
            f"<b>{player_name}</b> переходит из «{from_club}» в «{to_club}»!\n"
            f"💰 Сумма сделки: {fee_m:.1f} млн €.\n"
            f"Контракт подписан, медицинские тесты пройдены. Официальный анонс скоро! 🤝✍️"
        )
        return TransferNews(headline=f"{tag}: {player_name} → {to_club}!", body=text)


# ---------------------------------------------------------------------------
# 5. generate_training_report
# ---------------------------------------------------------------------------

async def generate_training_report(
    club_name: str,
    focus: str,
    players: List[dict],
    intensity: str = "normal",
) -> TrainingReport:
    """Generate coach training report."""
    prompt = f"""
Клуб: {club_name}
Фокус: {focus}
Количество игроков: {len(players)}
Интенсивность: {intensity}

Сгенерируй отчет тренера на русском языке и массив player_improvements:
Для каждого игрока: player_name, ovr_gain (0-1), xp_gain (15-60).
Игроки моложе 21 года получают удвоенный XP.
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=TrainingReport,
            system_instruction="Ты главный тренер команды Ла Лиги.",
        )
        data = _parse_json_response(response.text)
        return TrainingReport(**data)
    except Exception as exc:
        logger.warning("Training report fallback triggered: %s", exc)
        improvements = []
        for p in players:
            is_u21 = p.get("age", 25) < 21
            mult = 2 if is_u21 else 1
            xp = random.randint(15, 35) * mult
            improvements.append({
                "player_name": p["name"],
                "ovr_gain": 1 if xp > 50 and random.random() < 0.25 else 0,
                "xp_gain": xp,
            })
        return TrainingReport(
            focus=focus,
            report_text=f"Команда провела продуктивную тренировочную сессию в направлении «{focus}». Все игроки работали с высокой отдачей.",
            player_improvements=improvements,
            injury_risk_triggered=False,
            injured_player=None,
        )


# ---------------------------------------------------------------------------
# 6. generate_awards_ceremony
# ---------------------------------------------------------------------------

async def generate_awards_ceremony(
    season: int,
    standings: List[dict],
    top_scorers: List[dict],
    clubs_data: List[dict],
) -> AwardsCeremony:
    """Dramatic end of season ceremony with awards and satire."""
    champ = standings[0].get("club", "Чемпион") if standings else "Чемпион"
    last = standings[-1].get("club", "Аутсайдер") if standings else "Аутсайдер"
    top_scorer = top_scorers[0].get("player_name", "Бомбардир") if top_scorers else "Бомбардир"
    goals = top_scorers[0].get("goals", 25) if top_scorers else 25

    prompt = f"""
Проведи торжественную церемонию наград Ла Лиги AI Dynasty (Сезон {season}).
Чемпион: {champ}
Аутсайдер: {last}
Топ-бомбардир: {top_scorer} ({goals} голов)

Заполни все поля AwardsCeremony с пафосом, юмором и драматизмом на русском языке.
Особо выдели номинацию "Золотая калоша" (сатирический разнос худшего трансфера).
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=AwardsCeremony,
            system_instruction="Ты ведущий гала-вечера Ла Лиги.",
        )
        data = _parse_json_response(response.text)
        return AwardsCeremony(**data)
    except Exception as exc:
        logger.warning("Awards ceremony fallback triggered: %s", exc)
        return AwardsCeremony(
            ceremony_intro=f"Добро пожаловать на торжественную церемонию закрытия {season}-го сезона Ла Лиги AI Dynasty! Это был невероятный год футбола!",
            ballon_dor_winner=top_scorer,
            ballon_dor_text=f"Феноменальный сезон! Абсолютный лидер атаки и мозг своей команды.",
            golden_boy_winner="Молодой Талант",
            golden_boy_text="Восходящая звезда, покорившая испанские стадионы своей смелостью.",
            best_goalkeeper_winner="Надёжный Страж",
            best_goalkeeper_text="Железная стена в воротах, спасшая десятки сложнейших мячей.",
            best_team=champ,
            best_team_text=f"Заслуженное золото! Команда «{champ}» продемонстрировала чемпионский характер.",
            golden_boot_winner=top_scorer,
            golden_boot_goals=goals,
            flop_player="Дорогой Новичок",
            flop_text="Главное разочарование года: гигантские деньги, ноль полезных действий на поле.",
            breakthrough_club=standings[min(3, len(standings)-1)].get("club", "Прорыв"),
            breakthrough_text="Команда, превзошедшая любые ожидания экспертов и ворвавшаяся в элиту.",
            bus_master_club="Оборонительный Клуб",
            bus_master_text="Тактический эталон дисциплины: их оборону невозможно было вскрыть.",
            comeback_king_club="Король Камбэков",
            comeback_king_text="Мастера волевых концовок после 80-й минуты.",
            rebuilding_grant_club=last,
            rebuilding_grant_text=f"«{last}» получает специальный грант на перестройку. Не сдаваться!",
            closing_speech="Сезон официально закрыт! Впереди трансферное окно и подготовка к новым битвам!",
        )


# ---------------------------------------------------------------------------
# 7. Autonomous Next-Season Budget Engine (Upgrade #3)
# ---------------------------------------------------------------------------

async def calculate_next_season_budgets(
    season: int,
    standings: List[dict],
    awards_data: dict,
    clubs_data: List[dict],
) -> SeasonBudgetReport:
    """
    AI Autonomous Next-Season Budget Engine.
    Evaluates:
      a) Remaining balance from finished season (FFP tax if overspent).
      b) Placement prize + UEFA/TV Rights pool distribution.
      c) Trophies and awards won.
      d) Dynamic AI Sponsor Evaluation (overperformance bonus / underperformance cut).
    """
    prompt = f"""
Ты — Финансовый комитет Ла Лиги и AI-экономист футбола.
Рассчитай бюджеты на Сезон {season + 1} для всех 20 клубов Ла Лиги на основе результатов Сезона {season}.

Итоговая таблица:
{json.dumps([{ 'club': s.get('club', s.get('name')), 'pos': s.get('position', idx+1), 'pts': s.get('points', 0) } for idx, s in enumerate(standings[:20])], ensure_ascii=False)}

Клубы и их статус:
{json.dumps([{ 'name': c.get('name'), 'tier': c.get('tier', 2), 'balance': c.get('budget', 40000000) } for c in clubs_data[:20]], ensure_ascii=False)}

ФОРМУЛА БЮДЖЕТА:
1. remaining_balance: текущий баланс клуба. Если balance < 0, начисляется ffp_tax (штраф фэйр-плей от 20% до 50% дефицита).
2. placement_prize: 1-е место 140M, 2-е 120M, 3-е 100M, 4-е 85M, 5-е 72M, ... 20-е 6M.
3. tv_rights_share: от 20M до 45M в зависимости от популярности.
4. trophy_and_awards_bonus: Чемпион (+25M), Кубок (+15M), Золотой мяч (+10M), Прорыв года (+15M), Грант аутсайдера (+12M).
5. sponsor_contract_eur: Динамическая оценка спонсоров:
   - Если клуб из Tier 3 или Tier 2 занял высокое место (топ-4/топ-6), спонсорский контракт увеличивается на +25-40%!
   - Если гранд (Tier 1) провалился, спонсорский бонус срезается на 15-25%.
6. new_season_budget = remaining_balance - ffp_tax + placement_prize + tv_rights_share + trophy_and_awards_bonus + sponsor_contract_eur.

Верни строго JSON схемы SeasonBudgetReport для всех 20 клубов.
""".strip()

    try:
        response = await _generate_with_retry(
            prompt=prompt,
            response_schema=SeasonBudgetReport,
            system_instruction="Ты финансовый регулятор Ла Лиги.",
        )
        data = _parse_json_response(response.text)
        return SeasonBudgetReport(**data)
    except Exception as exc:
        logger.warning("Budget calculation fallback triggered: %s", exc)

        evals: List[ClubBudgetEvaluation] = []
        for idx, s in enumerate(standings[:20], 1):
            c_name = s.get("club") or s.get("name") or f"Клуб #{idx}"
            tier = 2
            curr_budget = 40_000_000.0
            for cd in clubs_data:
                if cd.get("name") == c_name:
                    tier = cd.get("tier", 2)
                    curr_budget = cd.get("budget", 40_000_000.0)
                    break

            # Placement prize scale
            prize_scale = {
                1: 140_000_000, 2: 120_000_000, 3: 100_000_000, 4: 85_000_000,
                5: 72_000_000, 6: 60_000_000, 7: 50_000_000, 8: 42_000_000,
                9: 36_000_000, 10: 30_000_000, 11: 26_000_000, 12: 23_000_000,
                13: 20_000_000, 14: 18_000_000, 15: 16_000_000, 16: 14_000_000,
                17: 12_000_000, 18: 10_000_000, 19: 8_000_000, 20: 6_000_000
            }
            placement_prize = float(prize_scale.get(idx, 15_000_000))
            tv_rights = float(random.randint(22_000_000, 45_000_000))

            ffp_tax = abs(curr_budget) * 0.3 if curr_budget < 0 else 0.0
            trophy_bonus = 25_000_000.0 if idx == 1 else (12_000_000.0 if idx == 20 else 0.0)

            # Dynamic sponsor
            if tier == 3 and idx <= 6:
                sponsor_status = "🔥 СЕНСАЦИЯ! Спонсорский контракт взлетел на +35%"
                sponsor_eur = 35_000_000.0
            elif tier == 1 and idx > 4:
                sponsor_status = "📉 Провал гиганта: спонсорские выплаты урезаны на -20%"
                sponsor_eur = 30_000_000.0
            elif idx <= 4:
                sponsor_status = "⭐ Зона Лиги Чемпионов: премиальный контракт (+20%)"
                sponsor_eur = 45_000_000.0
            else:
                sponsor_status = "Стабильный контракт Ла Лиги"
                sponsor_eur = 20_000_000.0

            new_budget = max(20_000_000.0, curr_budget - ffp_tax + placement_prize + tv_rights + trophy_bonus + sponsor_eur)

            evals.append(ClubBudgetEvaluation(
                club_name=c_name,
                remaining_balance=curr_budget,
                ffp_tax=ffp_tax,
                placement_prize=placement_prize,
                tv_rights_share=tv_rights,
                trophy_and_awards_bonus=trophy_bonus,
                sponsor_status=sponsor_status,
                sponsor_contract_eur=sponsor_eur,
                new_season_budget=new_budget,
                executive_summary=f"Итоговый бюджет: {new_budget/1e6:.1f}M€ с учётом спонсоров и призовых.",
            ))

        return SeasonBudgetReport(
            season_completed=season,
            new_season=season + 1,
            financial_overview=f"Финансовый совет Ла Лиги подвёл итоги {season}-го сезона. Призовые пулы и ТВ-контракты распределены, клубы получили бюджеты на Сезон {season + 1}.",
            club_evaluations=evals,
        )


# ---------------------------------------------------------------------------
# 8. generate_fabrizio_post (Plain text utility)
# ---------------------------------------------------------------------------

async def generate_fabrizio_post(event_type: str, details: dict) -> str:
    """Generate quick Fabrizio Romano plain string post."""
    details_str = ", ".join(f"{k}: {v}" for k, v in details.items())
    prompt = f"Напиши короткий (1-2 предложения) пост Фабрицио Романо на русском: {event_type}. Детали: {details_str}. Включи эмодзи."
    try:
        client = get_client()
        if client:
            await _rate_limiter.acquire()
            res = client.models.generate_content(
                model=MODEL_ID,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction="Ты Фабрицио Романо. Пиши коротко с эмодзи на русском.",
                ),
            )
            return res.text.strip()
    except Exception:
        pass
    return f"🚨 <b>Here we go!</b> Новое событие на трансферном рынке Ла Лиги: {details_str}! ✍️⚽"


__all__ = [
    "GoalEvent",
    "CardEvent",
    "InjuryEvent",
    "MatchSimulationResult",
    "PlayerValuation",
    "TransferNews",
    "TrainingReport",
    "AwardsCeremony",
    "ClubBudgetEvaluation",
    "SeasonBudgetReport",
    "simulate_match",
    "simulate_multiple_matches",
    "valuate_external_player",
    "generate_transfer_news",
    "generate_training_report",
    "generate_awards_ceremony",
    "calculate_next_season_budgets",
    "generate_fabrizio_post",
]
