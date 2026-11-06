import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from requests import RequestException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analytics import calculate_poisson_probabilities, calculate_value_edge
from app.database import StoredMatchPrediction, get_db, init_db
from app.fetcher import (
    APIConfigurationError,
    SportsAPIError,
    calculate_league_goal_averages,
    fetch_fixture,
    fetch_league_standings,
    fetch_leagues,
    fetch_recent_form,
    fetch_upcoming_fixtures,
    get_team_goal_averages,
    is_api_configured,
    summarize_team_form,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Soccer Match & Value Betting Analyzer",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/")
def read_root() -> dict[str, str | bool]:
    return {
        "status": "Active",
        "engine": "Poisson Match Analysis Engine",
        "sports_data_configured": is_api_configured(),
    }


def _handle_sports_api_error(error: Exception) -> None:
    if isinstance(error, APIConfigurationError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    logger.warning("Sports data request failed: %s", error)
    raise HTTPException(status_code=502, detail=str(error)) from error


@app.get("/leagues")
def leagues(
    country_name: str = Query(..., min_length=2, max_length=80),
    season: int = Query(..., ge=2000, le=2100),
) -> dict:
    try:
        results = fetch_leagues(country_name.strip(), season)
    except (APIConfigurationError, SportsAPIError, RequestException, ValueError) as error:
        _handle_sports_api_error(error)
    return {
        "country": country_name.strip(),
        "season": season,
        "leagues": [
            {
                "id": league["id"],
                "name": league["name"],
                "logo": league.get("logo"),
                "country": league.get("country", country_name.strip()),
                "seasons": [
                    item["season"]
                    for item in league.get("seasons", [])
                    if isinstance(item, dict) and isinstance(item.get("season"), int)
                ],
            }
            for league in results
            if isinstance(league.get("id"), int)
            and isinstance(league.get("name"), str)
        ],
    }


@app.get("/fixtures/upcoming")
def upcoming_fixtures(
    league_id: int = Query(..., gt=0),
    season: int = Query(..., ge=2000, le=2100),
    days: int = Query(30, ge=1, le=60),
) -> dict:
    try:
        fixtures = fetch_upcoming_fixtures(league_id, season, days)
    except (APIConfigurationError, SportsAPIError, RequestException, ValueError) as error:
        _handle_sports_api_error(error)
    return {
        "league_id": league_id,
        "season": season,
        "fixtures": fixtures,
    }


@app.get("/standings")
def standings(
    league_id: int = Query(..., gt=0),
    season: int = Query(..., ge=2000, le=2100),
) -> dict:
    try:
        rows = fetch_league_standings(league_id, season)
    except (APIConfigurationError, SportsAPIError, RequestException, ValueError) as error:
        _handle_sports_api_error(error)
    table = []
    for row in rows:
        team = row.get("team", {})
        total = row.get("total", {})
        home = row.get("home", {})
        away = row.get("away", {})
        if not isinstance(team, dict) or not isinstance(total, dict):
            continue
        table.append(
            {
                "position": row.get("position"),
                "team_id": team.get("id"),
                "team": team.get("name"),
                "logo": team.get("logo"),
                "played": total.get("games"),
                "wins": total.get("wins"),
                "draws": total.get("draws"),
                "losses": total.get("loses"),
                "goals_for": total.get("scoredGoals"),
                "goals_against": total.get("receivedGoals"),
                "goal_difference": (
                    total.get("scoredGoals", 0) - total.get("receivedGoals", 0)
                    if isinstance(total.get("scoredGoals"), int)
                    and isinstance(total.get("receivedGoals"), int)
                    else None
                ),
                "points": row.get("points"),
                "home_goals": home.get("scoredGoals"),
                "away_goals": away.get("scoredGoals"),
            }
        )
    return {
        "league_id": league_id,
        "season": season,
        "standings": sorted(
            table,
            key=lambda team: (
                team["position"] if isinstance(team["position"], int) else 9999
            ),
        ),
    }


@app.get("/predictions/recent")
def recent_predictions(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> dict:
    predictions = db.scalars(
        select(StoredMatchPrediction)
        .order_by(StoredMatchPrediction.id.desc())
        .limit(limit)
    ).all()
    return {
        "predictions": [
            {
                "id": prediction.id,
                "match_date": (
                    prediction.match_date.isoformat()
                    if prediction.match_date is not None
                    else None
                ),
                "home_team": prediction.home_team,
                "away_team": prediction.away_team,
                "home_xg": prediction.home_xg,
                "away_xg": prediction.away_xg,
                "prob_home_win": prediction.prob_home_win,
                "prob_draw": prediction.prob_draw,
                "prob_away_win": prediction.prob_away_win,
                "fair_home_odds": prediction.fair_home_odds,
                "bookie_home_odds": prediction.bookie_home_odds,
                "ev_percentage": prediction.ev_percentage,
                "is_value_bet": prediction.is_value_bet,
            }
            for prediction in predictions
        ]
    }


def _save_prediction(
    db: Session,
    home_team: str,
    away_team: str,
    home_xg: float,
    away_xg: float,
    probabilities: dict,
    bookie_home_odds: Optional[float],
    match_date: Optional[datetime],
) -> dict:
    ev = None
    is_value_bet = False
    if bookie_home_odds is not None:
        ev = calculate_value_edge(probabilities["1X2"]["home_win"], bookie_home_odds)
        is_value_bet = ev > 5.0

    prediction = StoredMatchPrediction(
        match_date=match_date,
        home_team=home_team.strip(),
        away_team=away_team.strip(),
        home_xg=round(home_xg, 2),
        away_xg=round(away_xg, 2),
        prob_home_win=probabilities["1X2"]["home_win"],
        prob_draw=probabilities["1X2"]["draw"],
        prob_away_win=probabilities["1X2"]["away_win"],
        fair_home_odds=probabilities["true_odds"]["home_win"],
        bookie_home_odds=bookie_home_odds,
        ev_percentage=ev,
        is_value_bet=is_value_bet,
    )
    try:
        db.add(prediction)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not save match prediction.")
        raise HTTPException(
            status_code=500, detail="Could not save match prediction."
        ) from error

    return {
        "match": f"{home_team.strip()} vs {away_team.strip()}",
        "expected_goals": {
            "home_xg": round(home_xg, 2),
            "away_xg": round(away_xg, 2),
        },
        "probabilities": probabilities["1X2"],
        "calculated_fair_odds": probabilities["true_odds"],
        "totals_market": probabilities["over_under_2_5"],
        "goal_markets": probabilities["over_under"],
        "both_teams_to_score": probabilities["both_teams_to_score"],
        "top_scorelines": probabilities["top_scorelines"],
        "value_analysis": {
            "bookie_home_odds": bookie_home_odds,
            "ev_percentage": ev,
            "home_ev_percentage": ev,
            "is_value_bet": is_value_bet,
        },
    }


@app.post("/analyze/match")
def analyze_match(
    home_team: str = Query(..., min_length=1, max_length=100),
    away_team: str = Query(..., min_length=1, max_length=100),
    home_attack_rating: float = Query(..., ge=0),
    away_defense_rating: float = Query(..., ge=0),
    away_attack_rating: float = Query(..., ge=0),
    home_defense_rating: float = Query(..., ge=0),
    league_avg_home_goals: float = Query(1.5, ge=0),
    league_avg_away_goals: float = Query(1.2, ge=0),
    bookie_home_odds: Optional[float] = Query(None, gt=1),
    match_date: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    if not home_team.strip() or not away_team.strip():
        raise HTTPException(
            status_code=422, detail="Team names must not be blank."
        )

    home_xg = home_attack_rating * away_defense_rating * league_avg_home_goals
    away_xg = away_attack_rating * home_defense_rating * league_avg_away_goals
    probabilities = calculate_poisson_probabilities(home_xg, away_xg)
    return _save_prediction(
        db,
        home_team,
        away_team,
        home_xg,
        away_xg,
        probabilities,
        bookie_home_odds,
        match_date,
    )


@app.post("/analyze/fixture")
def analyze_fixture(
    fixture_id: int = Query(..., gt=0),
    league_id: int = Query(..., gt=0),
    season: int = Query(..., ge=2000, le=2100),
    match_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    bookie_home_odds: Optional[float] = Query(None, gt=1),
    db: Session = Depends(get_db),
) -> dict:
    try:
        fixture = fetch_fixture(fixture_id, league_id, season, match_date)
        standings = fetch_league_standings(league_id, season)
        league_averages = calculate_league_goal_averages(standings)
        home_goals = get_team_goal_averages(standings, fixture["home"]["id"])
        away_goals = get_team_goal_averages(standings, fixture["away"]["id"])
        home_form = summarize_team_form(
            fetch_recent_form(fixture["home"]["id"]), fixture["home"]["id"]
        )
        away_form = summarize_team_form(
            fetch_recent_form(fixture["away"]["id"]), fixture["away"]["id"]
        )
    except (APIConfigurationError, SportsAPIError, RequestException, ValueError) as error:
        _handle_sports_api_error(error)

    league_home = league_averages["home"]
    league_away = league_averages["away"]
    if league_home <= 0 or league_away <= 0:
        raise HTTPException(
            status_code=502, detail="League goal averages are not available."
        )

    home_attack = home_goals["home"]["scored"] / league_home
    away_defense = away_goals["away"]["received"] / league_home
    away_attack = away_goals["away"]["scored"] / league_away
    home_defense = home_goals["home"]["received"] / league_away
    home_xg = home_attack * away_defense * league_home
    away_xg = away_attack * home_defense * league_away
    probabilities = calculate_poisson_probabilities(home_xg, away_xg)
    try:
        prediction_date = (
            datetime.fromisoformat(fixture["date"].replace("Z", "+00:00"))
            if fixture["date"]
            else None
        )
    except ValueError:
        prediction_date = None

    result = _save_prediction(
        db,
        fixture["home"]["name"],
        fixture["away"]["name"],
        home_xg,
        away_xg,
        probabilities,
        bookie_home_odds,
        prediction_date,
    )
    result["fixture_id"] = fixture_id
    result["data_source"] = (
        "football-data.org competition standings and recent finished matches"
    )
    result["league_averages"] = {
        "home_goals": round(league_home, 2),
        "away_goals": round(league_away, 2),
    }
    result["team_goal_averages"] = {
        "home": home_goals,
        "away": away_goals,
    }
    result["recent_form"] = {
        "home": home_form,
        "away": away_form,
    }
    return result
