"""
AI service layer — wraps Google Gemini for all LLM calls.
Uses google-genai SDK with structured output (Pydantic v2).
Includes rate-limiter with 3.5 s spacing and 4-stage backoff for HTTP 429.
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, List, Optional

from pydantic import BaseModel, Field

from config import GEMINI_API_KEY, MODEL_NAME

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Gemini client
# ---------------------------------------------------------------------------
_client = None

def _get_client():
    global _client
    if _client is None:
        try:
            from google import genai
            _client = genai.Client(api_key=GEMINI_API_KEY)
        except Exception as e:
            logger.error(f"Failed to init Gemini client: {e}")
    return _client


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
_last_call_time: float = 0.0
_rate_lock = asyncio.Lock()
_MIN_SPACING = 3.5  # seconds


async def _rate_limited_call(coro_factory, *args, **kwargs):
    """Call coro_factory() respecting rate limits and 429 backoff."""
    global _last_call_time
    backoff_delays = [5, 15, 30, 60]

    for attempt, delay in enumerate(backoff_delays + [None]):
        async with _rate_lock:
            now = time.monotonic()
            wait = _MIN_SPACING - (now - _last_call_time)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_call_time = time.monotonic()

        try:
            result = await coro_factory(*args, **kwargs)
            return result
        except Exception as e:
            err_str = str(e).lower()
            if "429" in err_str or "quota" in err_str or "rate" in err_str:
                if delay is None:
                    raise
                logger.warning(f"Rate limit hit, backing off {delay}s (attempt {attempt+1})")
                await asyncio.sleep(delay)
            else:
                raise

    raise RuntimeError("Exceeded max retry attempts for Gemini API")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class GoalEvent(BaseModel):
    player_name: str = "Игрок"
    minute: int = 0


class CardEvent(BaseModel):
    player_name: str = "Игрок"
    minute: int = 0
    club: str = ""


class InjuryEvent(BaseModel):
    player_name: str = "Игрок"
    rounds_out: int = 1


class MatchSimulationResult(BaseModel):
    home_goals: int = 0
    away_goals: int = 0
    scorers_home: List[GoalEvent] = Field(default_factory=list)
    scorers_away: List[GoalEvent] = Field(default_factory=list)
    possession_home: int = 50
    shots_on_target_home: int = 4
    shots_on_target_away: int = 4
    yellow_cards_home: List[CardEvent] = Field(default_factory=list)
    yellow_cards_away: List[CardEvent] = Field(default_factory=list)
    red_cards_home: List[CardEvent] = Field(default_factory=list)
    red_cards_away: List[CardEvent] = Field(default_factory=list)
    injuries_home: List[InjuryEvent] = Field(default_factory=list)
    injuries_away: List[InjuryEvent] = Field(default_factory=list)
    mvp: str = "Лучший игрок матча"
    narrative_report: str = "Матч сыгран."

    def get(self, key, default=None):  # noqa: ANN001
        return getattr(self, key, default)

    @property
    def scorers(self) -> list:
        result = []
        for s in self.scorers_home:
            result.append({"name": s.player_name, "minute": s.minute, "side": "home"})
        for s in self.scorers_away:
            result.append({"name": s.player_name, "minute": s.minute, "side": "away"})
        return result

    @property
    def yellow_cards(self) -> list:
        result = []
        for c in self.yellow_cards_home:
            result.append({"name": c.player_name, "minute": c.minute, "side": "home"})
        for c in self.yellow_cards_away:
            result.append({"name": c.player_name, "minute": c.minute, "side": "away"})
        return result

    @property
    def red_cards(self) -> list:
        result = []
        for c in self.red_cards_home:
            result.append({"name": c.player_name, "minute": c.minute, "side": "home"})
        for c in self.red_cards_away:
            result.append({"name": c.player_name, "minute": c.minute, "side": "away"})
        return result

    @property
    def injuries(self) -> list:
        result = []
        for i in self.injuries_home:
            result.append({"name": i.player_name, "rounds_out": i.rounds_out, "side": "home"})
        for i in self.injuries_away:
            result.append({"name": i.player_name, "rounds_out": i.rounds_out, "side": "away"})
        return result

    @property
    def narrative(self) -> str:
        return self.narrative_report

    @property
    def shots_home(self) -> int:
        return self.shots_on_target_home

    @property
    def shots_away(self) -> int:
        return self.shots_on_target_away


class PlayerValuation(BaseModel):
    player_name: str = "Неизвестный игрок"
    position: str = "CM"
    age: int = 24
    ovr: int = 72
    nationality: str = "испанец"
    market_value: int = 10_000_000
    markup_value: int = 15_000_000
    description: str = "Талантливый футболист."


class TransferNews(BaseModel):
    headline: str = "Трансфер завершён!"
    body: str = "Игрок переходит в новый клуб."
    fabrizio_quote: str = "Here we go! ✅"


class TrainingImprovements(BaseModel):
    player_name: str
    ovr_gain: int = 0
    injury_risk: bool = False


class TrainingReport(BaseModel):
    summary: str = "Тренировка прошла успешно."
    highlight: str = "Команда хорошо поработала."
    improvements: List[TrainingImprovements] = Field(default_factory=list)


class AwardWinner(BaseModel):
    award_name: str
    player_name: str
    club_name: str
    description: str = ""


class AwardsCeremony(BaseModel):
    ceremony_title: str = "Церемония награждения La Liga"
    best_player: str = "Лучший игрок сезона"
    best_goalkeeper: str = "Лучший вратарь"
    best_young_player: str = "Лучший молодой игрок"
    top_scorer: str = "Лучший бомбардир"
    best_manager: str = "Лучший тренер"
    awards: List[AwardWinner] = Field(default_factory=list)
    speech: str = "Поздравляем всех победителей!"


# ---------------------------------------------------------------------------
# Internal async Gemini call helper
# ---------------------------------------------------------------------------

async def _call_gemini_structured(prompt: str, response_model: type, fast_mode: bool = False) -> Any:
    """
    Call Gemini with a structured output schema.
    Returns a parsed Pydantic model instance.
    """
    client = _get_client()
    if client is None:
        raise RuntimeError("Gemini client not initialised")

    schema = response_model.model_json_schema()

    async def _do_call():
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                    "temperature": 0.8 if not fast_mode else 0.5,
                },
            ),
        )

    response = await _rate_limited_call(_do_call)
    raw_text = response.text
    data = json.loads(raw_text)
    return response_model.model_validate(data)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _fallback_match_result(home_team: str, away_team: str) -> MatchSimulationResult:
    """Return a simple random result when AI is unavailable."""
    hg = random.randint(0, 4)
    ag = random.randint(0, 4)
    return MatchSimulationResult(
        home_goals=hg,
        away_goals=ag,
        possession_home=random.randint(40, 60),
        shots_on_target_home=random.randint(2, 8),
        shots_on_target_away=random.randint(2, 8),
        mvp=f"Игрок {home_team}",
        narrative_report=f"Матч между {home_team} и {away_team} завершился со счётом {hg}:{ag}.",
    )


async def simulate_match(
    home_team: dict,
    away_team: dict,
    fast_mode: bool = False,
) -> MatchSimulationResult:
    """
    Simulate a single match using Gemini.
    Falls back to random result on error.
    """
    home_name = home_team.get("name", "Хозяева")
    away_name = away_team.get("name", "Гости")
    home_ovr = home_team.get("avg_ovr", 75)
    away_ovr = away_team.get("avg_ovr", 75)
    home_tactics = home_team.get("tactics", "4-4-2")
    away_tactics = away_team.get("tactics", "4-4-2")
    home_players_str = home_team.get("players_str", "")
    away_players_str = away_team.get("players_str", "")

    prompt = f"""Ты — движок симуляции футбольного матча Ла Лиги. Симулируй матч и верни JSON.

Хозяева: {home_name} (средний OVR: {home_ovr}, тактика: {home_tactics})
Игроки хозяев: {home_players_str}

Гости: {away_name} (средний OVR: {away_ovr}, тактика: {away_tactics})
Игроки гостей: {away_players_str}

Правила:
- Используй реальных игроков из списков для событий
- Количество голов: 0-5 для каждой команды (реалистично)
- 1-4 жёлтых карточки суммарно
- Красные карточки редки (10% вероятность)
- 0-1 травм суммарно
- narrative_report на русском (3-5 предложений)
- Владение мячом: от 35 до 65 для хозяев
- MVP — лучший игрок матча

{"Режим быстрой симуляции: только результат и MVP, краткий репортаж." if fast_mode else ""}
"""

    try:
        return await _call_gemini_structured(prompt, MatchSimulationResult, fast_mode)
    except Exception as e:
        logger.warning(f"Match simulation failed ({e}), using fallback")
        return _fallback_match_result(home_name, away_name)


async def simulate_multiple_matches(matches: list[dict]) -> list[MatchSimulationResult]:
    """Simulate multiple matches sequentially with rate limiting."""
    results = []
    for m in matches:
        result = await simulate_match(
            home_team=m.get("home", {}),
            away_team=m.get("away", {}),
            fast_mode=True,
        )
        results.append(result)
    return results


async def valuate_external_player(name: str, context: str = "") -> PlayerValuation:
    """Ask Gemini to generate stats and market value for an external player."""
    prompt = f"""Ты — скаут футбольного клуба Ла Лиги. Оцени игрока для трансфера.

Имя игрока: {name}
Контекст: {context}

Создай реалистичные характеристики для футболиста с таким именем:
- position: позиция (GK/CB/LB/RB/CDM/CM/CAM/LW/RW/ST)
- age: возраст (18-32)
- ovr: общий рейтинг (60-92)
- nationality: национальность
- market_value: рыночная стоимость в евро (целое число)
- markup_value: цена покупки с наценкой агента (на 20-40% больше market_value)
- description: краткое описание на русском
"""
    try:
        return await _call_gemini_structured(prompt, PlayerValuation)
    except Exception as e:
        logger.warning(f"Player valuation failed ({e}), using fallback")
        return PlayerValuation(
            player_name=name,
            ovr=random.randint(65, 82),
            market_value=random.randint(5_000_000, 40_000_000),
            markup_value=random.randint(7_000_000, 50_000_000),
        )


async def generate_transfer_news(transfer_info: dict) -> TransferNews:
    """Generate Fabrizio Romano-style transfer announcement in Russian."""
    player = transfer_info.get("player_name", "Игрок")
    from_club = transfer_info.get("from_club", "неизвестный клуб")
    to_club = transfer_info.get("to_club", "неизвестный клуб")
    fee = transfer_info.get("fee", 0)
    fee_m = fee / 1_000_000

    prompt = f"""Ты — Фабрицио Романо, главный инсайдер трансферного рынка. Пиши на РУССКОМ языке.

Трансфер: {player} переходит из {from_club} в {to_club} за €{fee_m:.1f}М

Создай:
1. headline — броский заголовок новости
2. body — 2-3 предложения о трансфере в стиле Романо (детали, причины)  
3. fabrizio_quote — фирменная цитата (обязательно включи "Here we go! ✅" в конце)

Всё на русском кроме "Here we go! ✅"
"""
    try:
        return await _call_gemini_structured(prompt, TransferNews)
    except Exception as e:
        logger.warning(f"Transfer news generation failed ({e})")
        return TransferNews(
            headline=f"🚨 {player} переходит в {to_club}!",
            body=f"{player} покидает {from_club} и присоединяется к {to_club} за €{fee_m:.1f}М.",
            fabrizio_quote=f"{player} в {to_club}, вот и всё! Here we go! ✅",
        )


async def generate_training_report(club: dict, focus: str, players: list[dict]) -> TrainingReport:
    """Generate a training session report with player improvements."""
    club_name = club.get("name", "Клуб")
    player_list = ", ".join(
        f"{p.get('name','?')} ({p.get('position','?')}, OVR {p.get('ovr',70)})"
        for p in players[:10]
    )

    prompt = f"""Ты — тренировочный аналитик футбольного клуба Ла Лиги. Пиши на РУССКОМ.

Клуб: {club_name}
Фокус тренировки: {focus}
Игроки: {player_list}

Создай отчёт о тренировке:
- summary: общее описание тренировки (2-3 предложения)
- highlight: главное достижение тренировки
- improvements: список улучшений для 2-4 игроков
  - player_name: имя игрока из списка выше
  - ovr_gain: прирост OVR (0-2)
  - injury_risk: получил ли травму на тренировке (true/false, редко)

Фокус "{focus}" влияет на то, кто улучшается:
- Атака: нападающие и атакующие полузащитники
- Оборона: защитники и вратари
- Физика: все игроки поровну
- Академия: только молодые игроки (до 22 лет)
"""
    try:
        return await _call_gemini_structured(prompt, TrainingReport)
    except Exception as e:
        logger.warning(f"Training report generation failed ({e})")
        improved = random.sample(players[:5], min(2, len(players)))
        improvements = [
            TrainingImprovements(player_name=p["name"], ovr_gain=random.randint(0, 1))
            for p in improved
        ]
        return TrainingReport(
            summary=f"Тренировка {club_name} с фокусом на {focus} прошла продуктивно.",
            highlight="Команда отлично поработала над ключевыми аспектами игры.",
            improvements=improvements,
        )


async def run_awards_ceremony(season_stats: dict) -> AwardsCeremony:
    """Run AI-powered end-of-season awards ceremony."""
    prompt = f"""Ты — ведущий церемонии награждения Ла Лиги. Пиши на РУССКОМ языке.

Статистика сезона:
{json.dumps(season_stats, ensure_ascii=False, indent=2)}

Объяви победителей наград:
- ceremony_title: название церемонии
- best_player: лучший игрок сезона (имя + клуб)
- best_goalkeeper: лучший вратарь
- best_young_player: лучший молодой игрок (до 23 лет)
- top_scorer: лучший бомбардир
- best_manager: лучший менеджер
- awards: список наград (award_name, player_name, club_name, description)
- speech: торжественная речь ведущего (3-4 предложения)
"""
    try:
        return await _call_gemini_structured(prompt, AwardsCeremony)
    except Exception as e:
        logger.warning(f"Awards ceremony generation failed ({e})")
        champion = season_stats.get("champion", "Чемпион")
        return AwardsCeremony(
            ceremony_title=f"🏆 Награды Ла Лиги — Сезон {season_stats.get('season', '?')}",
            best_player=season_stats.get("top_scorer_name", "Лучший игрок"),
            speech=f"Поздравляем {champion} с чемпионством! Незабываемый сезон!",
        )


async def calculate_next_season_budgets(
    standings: list[dict],
    clubs: list[dict],
    awards: Optional[AwardsCeremony] = None,
) -> dict[int, int]:
    """
    Calculate next season budgets based on league position, prize money and AI analysis.
    Returns a dict mapping club_id -> budget.
    """
    from config import LEAGUE_PRIZES

    budgets: dict[int, int] = {}
    for pos, row in enumerate(standings, start=1):
        club_id = row.get("club_id") or row.get("id")
        prize = LEAGUE_PRIZES.get(pos, 800_000)
        # Find current budget
        club = next((c for c in clubs if c["id"] == club_id), None)
        current_budget = club["budget"] if club else 20_000_000
        new_budget = current_budget + prize
        budgets[club_id] = new_budget

    return budgets
