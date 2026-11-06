import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from firebase_admin import auth as firebase_auth
from firebase_admin import get_app as get_firebase_app
from firebase_admin import initialize_app as initialize_firebase_app
from firebase_admin.exceptions import FirebaseError
from google.auth.exceptions import GoogleAuthError
from pydantic import BaseModel, Field
from requests import RequestException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.analytics import calculate_poisson_probabilities, calculate_value_edge
from app.auth import FIREBASE_PROJECT_ID
from app.database import (
    PredictionSlate,
    StoredMatchPrediction,
    UserAccount,
    get_db,
    init_db,
)
from app.fetcher import (
    APIConfigurationError,
    SportsAPIError,
    calculate_league_goal_averages,
    fetch_fixture,
    fetch_league_standings,
    fetch_leagues,
    fetch_match_result,
    fetch_recent_form,
    fetch_upcoming_fixtures,
    get_team_goal_averages,
    is_api_configured,
    summarize_team_form,
)

logger = logging.getLogger(__name__)


class SlateFixtureSelection(BaseModel):
    fixture_id: int = Field(gt=0)
    league_id: int = Field(gt=0)
    season: int = Field(ge=2000, le=2100)
    match_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    bookie_home_odds: float | None = Field(default=None, gt=1, le=100)


class CreateSlateRequest(BaseModel):
    name: str = Field(default="Matchday slate", min_length=1, max_length=120)
    fixtures: list[SlateFixtureSelection] = Field(min_length=1, max_length=40)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Soccer Match & Value Betting Analyzer",
    version="1.0.0",
    lifespan=lifespan,
)


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> UserAccount:
    if not FIREBASE_PROJECT_ID:
        raise HTTPException(
            status_code=503,
            detail="Firebase sign-in is not configured on the API server.",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Sign in to continue.")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    try:
        firebase_app = get_firebase_app()
    except ValueError:
        try:
            firebase_app = initialize_firebase_app(
                options={"projectId": FIREBASE_PROJECT_ID}
            )
        except (ValueError, FirebaseError, GoogleAuthError) as error:
            logger.exception("Could not initialize Firebase Admin.")
            raise HTTPException(
                status_code=503,
                detail="Firebase server credentials are not configured.",
            ) from error
    try:
        claims = firebase_auth.verify_id_token(token, app=firebase_app)
    except GoogleAuthError as error:
        logger.exception("Could not verify Firebase token because server credentials are unavailable.")
        raise HTTPException(
            status_code=503,
            detail="Firebase server credentials are not configured.",
        ) from error
    except (ValueError, FirebaseError) as error:
        raise HTTPException(
            status_code=401,
            detail="Your sign-in is invalid or expired. Sign in again.",
        ) from error

    firebase_uid = claims.get("uid") or claims.get("sub")
    email = claims.get("email")
    if (
        not isinstance(firebase_uid, str)
        or not isinstance(email, str)
        or claims.get("email_verified") is not True
    ):
        raise HTTPException(
            status_code=403,
            detail="Verify your email address before using Touchline.",
        )
    email = email.strip().casefold()
    display_name = str(claims.get("name") or email)[:200]
    picture_url = claims.get("picture")

    user = db.scalar(
        select(UserAccount).where(UserAccount.firebase_uid == firebase_uid)
    )
    if user is None:
        user = db.scalar(
            select(UserAccount)
            .where(
                func.lower(UserAccount.email) == email,
                UserAccount.firebase_uid.is_(None),
            )
            .order_by(UserAccount.id.asc())
        )
    account_changed = user is None
    if user is None:
        user = UserAccount(
            identity_subject=f"firebase:{firebase_uid}",
            firebase_uid=firebase_uid,
            email=email,
            display_name=display_name,
            picture_url=picture_url,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user)
    else:
        account_changed = (
            user.firebase_uid != firebase_uid
            or user.email != email
            or user.display_name != display_name
            or user.picture_url != picture_url
        )
        user.firebase_uid = firebase_uid
        user.email = email
        user.display_name = display_name
        user.picture_url = picture_url
    try:
        if account_changed:
            db.commit()
            db.refresh(user)
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not save the authenticated Firebase account.")
        raise HTTPException(
            status_code=500, detail="Could not load your account."
        ) from error
    return user


@app.get("/")
def read_root() -> dict[str, str | bool]:
    return {
        "status": "Active",
        "engine": "Poisson Match Analysis Engine",
        "sports_data_configured": is_api_configured(),
    }


@app.get("/auth/me")
def current_account(
    user: UserAccount = Depends(get_current_user),
) -> dict[str, int | str | None]:
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "picture_url": user.picture_url,
    }


def _outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "HOME"
    if home_goals < away_goals:
        return "AWAY"
    return "DRAW"


def _model_fixture(
    fixture: dict, standings: list[dict]
) -> tuple[float, float, dict, dict, dict]:
    league_averages = calculate_league_goal_averages(standings)
    home_goals = get_team_goal_averages(standings, fixture["home"]["id"])
    away_goals = get_team_goal_averages(standings, fixture["away"]["id"])
    league_home, league_away = league_averages["home"], league_averages["away"]
    if league_home <= 0 or league_away <= 0:
        raise ValueError("League goal averages are not available.")
    home_attack = home_goals["home"]["scored"] / league_home
    away_defense = away_goals["away"]["received"] / league_home
    away_attack = away_goals["away"]["scored"] / league_away
    home_defense = home_goals["home"]["received"] / league_away
    home_xg = home_attack * away_defense * league_home
    away_xg = away_attack * home_defense * league_away
    return (
        home_xg,
        away_xg,
        calculate_poisson_probabilities(home_xg, away_xg),
        league_averages,
        {"home": home_goals, "away": away_goals},
    )


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
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict:
    predictions = db.scalars(
        select(StoredMatchPrediction)
        .where(StoredMatchPrediction.user_id == user.id)
        .order_by(StoredMatchPrediction.id.desc())
        .limit(limit)
    ).all()
    return {
        "predictions": [
            {
                "id": prediction.id,
                "slate_id": prediction.slate_id,
                "fixture_id": prediction.fixture_id,
                "league_id": prediction.league_id,
                "season": prediction.season,
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
                "actual_home_goals": prediction.actual_home_goals,
                "actual_away_goals": prediction.actual_away_goals,
                "actual_outcome": prediction.actual_outcome,
            }
            for prediction in predictions
        ]
    }


@app.delete("/predictions/{prediction_id}")
def delete_prediction(
    prediction_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict[str, int | bool]:
    prediction = db.scalar(
        select(StoredMatchPrediction).where(
            StoredMatchPrediction.id == prediction_id,
            StoredMatchPrediction.user_id == user.id,
        )
    )
    if prediction is None:
        raise HTTPException(status_code=404, detail="Saved prediction not found.")
    slate_id = prediction.slate_id
    try:
        db.delete(prediction)
        if (
            slate_id is not None
            and db.scalar(
                select(func.count(StoredMatchPrediction.id)).where(
                    StoredMatchPrediction.slate_id == slate_id,
                    StoredMatchPrediction.user_id == user.id,
                    StoredMatchPrediction.id != prediction_id,
                    StoredMatchPrediction.id != prediction_id,
                )
            )
            == 0
        ):
            slate = db.scalar(
                select(PredictionSlate).where(
                    PredictionSlate.id == slate_id,
                    PredictionSlate.user_id == user.id,
                )
            )
            if slate is not None:
                db.delete(slate)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not delete saved prediction %s.", prediction_id)
        raise HTTPException(
            status_code=500, detail="Could not delete saved prediction."
        ) from error
    return {"deleted": True, "id": prediction_id}


@app.delete("/predictions")
def delete_all_predictions(
    confirm: bool = Query(False),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict[str, int | bool]:
    if not confirm:
        raise HTTPException(
            status_code=400,
            detail="Set confirm=true to delete all saved predictions.",
        )
    try:
        result = db.execute(
            delete(StoredMatchPrediction).where(
                StoredMatchPrediction.user_id == user.id
            )
        )
        db.execute(
            delete(PredictionSlate).where(PredictionSlate.user_id == user.id)
        )
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not delete all saved predictions.")
        raise HTTPException(
            status_code=500, detail="Could not delete saved predictions."
        ) from error
    return {"deleted": True, "count": result.rowcount or 0}


def _save_prediction(
    db: Session,
    user_id: int,
    home_team: str,
    away_team: str,
    home_xg: float,
    away_xg: float,
    probabilities: dict,
    bookie_home_odds: Optional[float],
    match_date: Optional[datetime],
    fixture_id: Optional[int] = None,
    league_id: Optional[int] = None,
    season: Optional[int] = None,
    slate_id: Optional[int] = None,
) -> dict:
    ev = None
    is_value_bet = False
    if bookie_home_odds is not None:
        ev = calculate_value_edge(probabilities["1X2"]["home_win"], bookie_home_odds)
        is_value_bet = ev > 5.0

    prediction = StoredMatchPrediction(
        user_id=user_id,
        slate_id=slate_id,
        fixture_id=fixture_id,
        league_id=league_id,
        season=season,
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
        "prediction_id": prediction.id,
        "slate_id": slate_id,
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
    user: UserAccount = Depends(get_current_user),
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
        user.id,
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
    user: UserAccount = Depends(get_current_user),
) -> dict:
    try:
        fixture = fetch_fixture(fixture_id, league_id, season, match_date)
        standings = fetch_league_standings(league_id, season)
        (
            home_xg,
            away_xg,
            probabilities,
            league_averages,
            team_averages,
        ) = _model_fixture(fixture, standings)
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
        user.id,
        fixture["home"]["name"],
        fixture["away"]["name"],
        home_xg,
        away_xg,
        probabilities,
        bookie_home_odds,
        prediction_date,
        fixture_id=fixture_id,
        league_id=league_id,
        season=season,
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
        "home": team_averages["home"],
        "away": team_averages["away"],
    }
    result["recent_form"] = {
        "home": home_form,
        "away": away_form,
    }
    return result


@app.post("/predictions/slates")
def create_prediction_slate(
    payload: CreateSlateRequest,
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict:
    keys = [
        (selection.league_id, selection.season, selection.fixture_id)
        for selection in payload.fixtures
    ]
    if len(keys) != len(set(keys)):
        raise HTTPException(status_code=422, detail="A fixture can only appear once.")

    standings_cache: dict[tuple[int, int], list[dict]] = {}
    forecasts = []
    failures = []
    for selection in payload.fixtures:
        cache_key = (selection.league_id, selection.season)
        try:
            fixture = fetch_fixture(
                selection.fixture_id,
                selection.league_id,
                selection.season,
                selection.match_date,
            )
            if cache_key not in standings_cache:
                standings_cache[cache_key] = fetch_league_standings(*cache_key)
            home_xg, away_xg, probabilities, _, _ = _model_fixture(
                fixture, standings_cache[cache_key]
            )
            match_date = datetime.fromisoformat(
                fixture["date"].replace("Z", "+00:00")
            )
            forecasts.append(
                {
                    "selection": selection,
                    "fixture": fixture,
                    "home_xg": home_xg,
                    "away_xg": away_xg,
                    "probabilities": probabilities,
                    "match_date": match_date,
                    "bookie_home_odds": selection.bookie_home_odds,
                }
            )
        except (
            APIConfigurationError,
            SportsAPIError,
            RequestException,
            ValueError,
        ) as error:
            failures.append(
                {"fixture_id": selection.fixture_id, "error": str(error)}
            )

    if not forecasts:
        return {"slate": None, "predictions": [], "failures": failures}

    slate = PredictionSlate(
        user_id=user.id,
        name=payload.name.strip() or "Matchday slate",
        created_at=datetime.now(timezone.utc),
    )
    try:
        db.add(slate)
        db.flush()
        predictions = []
        for forecast in forecasts:
            fixture = forecast["fixture"]
            probabilities = forecast["probabilities"]
            bookmaker_odds = forecast["bookie_home_odds"]
            ev = (
                calculate_value_edge(
                    probabilities["1X2"]["home_win"], bookmaker_odds
                )
                if bookmaker_odds is not None
                else None
            )
            prediction = StoredMatchPrediction(
                user_id=user.id,
                slate_id=slate.id,
                fixture_id=forecast["selection"].fixture_id,
                league_id=forecast["selection"].league_id,
                season=forecast["selection"].season,
                match_date=forecast["match_date"],
                home_team=fixture["home"]["name"],
                away_team=fixture["away"]["name"],
                home_xg=round(forecast["home_xg"], 2),
                away_xg=round(forecast["away_xg"], 2),
                prob_home_win=probabilities["1X2"]["home_win"],
                prob_draw=probabilities["1X2"]["draw"],
                prob_away_win=probabilities["1X2"]["away_win"],
                fair_home_odds=probabilities["true_odds"]["home_win"],
                bookie_home_odds=bookmaker_odds,
                ev_percentage=ev,
                is_value_bet=ev is not None and ev > 5.0,
            )
            db.add(prediction)
            predictions.append(prediction)
        db.commit()
        for prediction in predictions:
            db.refresh(prediction)
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not save prediction slate.")
        raise HTTPException(
            status_code=500, detail="Could not save the prediction slate."
        ) from error

    return {
        "slate": {
            "id": slate.id,
            "name": slate.name,
            "created_at": slate.created_at.isoformat(),
            "prediction_count": len(predictions),
        },
        "predictions": [
            {
                "id": prediction.id,
                "fixture_id": prediction.fixture_id,
                "match": f"{prediction.home_team} vs {prediction.away_team}",
                "expected_goals": {
                    "home_xg": prediction.home_xg,
                    "away_xg": prediction.away_xg,
                },
                "probabilities": {
                    "home_win": prediction.prob_home_win,
                    "draw": prediction.prob_draw,
                    "away_win": prediction.prob_away_win,
                },
                "calculated_fair_odds": {
                    "home_win": prediction.fair_home_odds
                },
                "value_analysis": {
                    "bookie_home_odds": prediction.bookie_home_odds,
                    "ev_percentage": prediction.ev_percentage,
                    "is_value_bet": prediction.is_value_bet,
                },
            }
            for prediction in predictions
        ],
        "failures": failures,
    }


@app.get("/predictions/slates")
def list_prediction_slates(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict:
    rows = db.execute(
        select(
            PredictionSlate,
            func.count(StoredMatchPrediction.id).label("prediction_count"),
            func.count(StoredMatchPrediction.actual_outcome).label("settled_count"),
        )
        .outerjoin(
            StoredMatchPrediction,
            StoredMatchPrediction.slate_id == PredictionSlate.id,
        )
        .where(PredictionSlate.user_id == user.id)
        .group_by(PredictionSlate.id)
        .order_by(PredictionSlate.id.desc())
        .limit(limit)
    ).all()
    return {
        "slates": [
            {
                "id": slate.id,
                "name": slate.name,
                "created_at": slate.created_at.isoformat(),
                "prediction_count": prediction_count,
                "settled_count": settled_count,
            }
            for slate, prediction_count, settled_count in rows
        ]
    }


@app.delete("/predictions/slates/{slate_id}")
def delete_prediction_slate(
    slate_id: int = Path(..., gt=0),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict[str, int | bool]:
    slate = db.scalar(
        select(PredictionSlate).where(
            PredictionSlate.id == slate_id,
            PredictionSlate.user_id == user.id,
        )
    )
    if slate is None:
        raise HTTPException(status_code=404, detail="Saved slate not found.")
    try:
        db.execute(
            delete(StoredMatchPrediction).where(
                StoredMatchPrediction.slate_id == slate_id,
                StoredMatchPrediction.user_id == user.id,
            )
        )
        db.delete(slate)
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not delete prediction slate %s.", slate_id)
        raise HTTPException(
            status_code=500, detail="Could not delete the prediction slate."
        ) from error
    return {"deleted": True, "id": slate_id}


@app.post("/predictions/results/refresh")
def refresh_prediction_results(
    limit: int = Query(15, ge=1, le=20),
    slate_id: int | None = Query(None, gt=0),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict:
    statement = select(StoredMatchPrediction).where(
        StoredMatchPrediction.user_id == user.id,
        StoredMatchPrediction.fixture_id.is_not(None),
        StoredMatchPrediction.actual_outcome.is_(None),
        StoredMatchPrediction.match_date <= datetime.now(timezone.utc),
    )
    if slate_id is not None:
        statement = statement.where(StoredMatchPrediction.slate_id == slate_id)
    predictions = db.scalars(
        statement.order_by(StoredMatchPrediction.id.asc()).limit(limit)
    ).all()
    updated = []
    failures = []
    for prediction in predictions:
        try:
            result = fetch_match_result(prediction.fixture_id)
            home_goals, away_goals = result["home_goals"], result["away_goals"]
            if (
                result["status"] == "FINISHED"
                and isinstance(home_goals, int)
                and isinstance(away_goals, int)
            ):
                prediction.actual_home_goals = home_goals
                prediction.actual_away_goals = away_goals
                prediction.actual_outcome = _outcome(home_goals, away_goals)
                prediction.settled_at = datetime.now(timezone.utc)
                updated.append(prediction.id)
        except (
            APIConfigurationError,
            SportsAPIError,
            RequestException,
            ValueError,
        ) as error:
            failures.append({"prediction_id": prediction.id, "error": str(error)})
    try:
        db.commit()
    except SQLAlchemyError as error:
        db.rollback()
        logger.exception("Could not save refreshed match results.")
        raise HTTPException(
            status_code=500, detail="Could not save match results."
        ) from error

    pending_count = db.scalar(
        select(func.count(StoredMatchPrediction.id)).where(
            StoredMatchPrediction.user_id == user.id,
            StoredMatchPrediction.fixture_id.is_not(None),
            StoredMatchPrediction.actual_outcome.is_(None),
        )
    )
    return {
        "updated_count": len(updated),
        "updated_prediction_ids": updated,
        "pending_count": pending_count or 0,
        "failures": failures,
    }


@app.get("/predictions/performance")
def prediction_performance(
    db: Session = Depends(get_db),
    user: UserAccount = Depends(get_current_user),
) -> dict:
    settled = db.scalars(
        select(StoredMatchPrediction).where(
            StoredMatchPrediction.user_id == user.id,
            StoredMatchPrediction.actual_outcome.is_not(None),
        )
    ).all()
    pending_statement = select(func.count(StoredMatchPrediction.id)).where(
        StoredMatchPrediction.user_id == user.id,
        StoredMatchPrediction.fixture_id.is_not(None),
        StoredMatchPrediction.actual_outcome.is_(None),
    )
    pending = db.scalar(pending_statement) or 0
    eligible = (
        db.scalar(
            pending_statement.where(
                StoredMatchPrediction.match_date <= datetime.now(timezone.utc)
            )
        )
        or 0
    )
    correct = 0
    squared_errors = 0.0
    for prediction in settled:
        probabilities = (
            (prediction.prob_home_win, "HOME"),
            (prediction.prob_draw, "DRAW"),
            (prediction.prob_away_win, "AWAY"),
        )
        predicted = max(probabilities, key=lambda item: item[0])[1]
        correct += predicted == prediction.actual_outcome
        squared_errors += sum(
            (probability - (outcome == prediction.actual_outcome)) ** 2
            for probability, outcome in probabilities
        )
    return {
        "settled_count": len(settled),
        "correct_outcomes": correct,
        "pending_results": pending,
        "eligible_for_settlement": eligible,
        "accuracy": correct / len(settled) if settled else None,
        "mean_brier_score": (
            squared_errors / len(settled) if settled else None
        ),
        "note": (
            "1X2 top-pick accuracy and multiclass Brier score are historical "
            "descriptions, not predictions of future performance."
        ),
    }
