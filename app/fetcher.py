import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")
BASE_URL = os.getenv(
    "FOOTBALL_DATA_BASE_URL", "https://api.football-data.org/v4"
).rstrip("/")
TIMEOUT_SECONDS = 15


class APIConfigurationError(RuntimeError):
    pass


class SportsAPIError(RuntimeError):
    pass


def is_api_configured() -> bool:
    return bool(API_KEY and API_KEY != "YOUR_FOOTBALL_DATA_API_KEY_HERE")


def _fetch(endpoint: str, params: dict[str, Any] | None = None) -> Any:
    if not is_api_configured():
        raise APIConfigurationError(
            "Set a valid FOOTBALL_DATA_API_KEY in the project .env file."
        )

    try:
        response = requests.get(
            f"{BASE_URL}/{endpoint.lstrip('/')}",
            headers={"X-Auth-Token": API_KEY},
            params=params or {},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else "unknown"
        detail = "request rejected"
        if error.response is not None:
            try:
                body = error.response.json()
                detail = body.get("message") or body.get("error") or detail
            except ValueError:
                pass
        raise SportsAPIError(
            f"football-data.org returned HTTP {status}: {detail}"
        ) from error
    except requests.RequestException as error:
        raise SportsAPIError("Could not reach football-data.org.") from error
    except ValueError as error:
        raise SportsAPIError("football-data.org returned invalid JSON.") from error

    if not isinstance(payload, dict):
        raise SportsAPIError("football-data.org returned an invalid response.")
    if payload.get("error"):
        raise SportsAPIError(f"football-data.org returned an error: {payload['error']}")
    return payload


def fetch_leagues(country_name: str, season: int) -> list[dict[str, Any]]:
    """Fetch competitions in a country that provide the requested season."""
    payload = _fetch("competitions")
    competitions = payload.get("competitions")
    if not isinstance(competitions, list):
        raise SportsAPIError("football-data.org returned an invalid competitions list.")

    results = []
    for competition in competitions:
        if not isinstance(competition, dict):
            continue
        area = competition.get("area") or {}
        if str(area.get("name", "")).casefold() != country_name.casefold():
            continue
        seasons = competition.get("seasons") or []
        years = sorted(
            {
                year
                for item in seasons
                if isinstance(item, dict)
                if (year := _season_year(item)) is not None
            }
        )
        current_season = competition.get("currentSeason")
        if isinstance(current_season, dict):
            year = _season_year(current_season)
            if year is not None and year not in years:
                years.append(year)
        if season not in years:
            continue
        results.append(
            {
                "id": competition.get("id"),
                "name": competition.get("name"),
                "logo": competition.get("emblem"),
                "country": area.get("name"),
                "code": competition.get("code"),
                "seasons": [{"season": year} for year in sorted(years)],
            }
        )
    return results


def _season_year(season: dict[str, Any]) -> int | None:
    for field in ("startDate", "endDate"):
        value = season.get(field)
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            return int(value[:4])
    return None


def fetch_upcoming_fixtures(
    league_id: int, season: int, days: int = 30
) -> list[dict[str, Any]]:
    """Fetch scheduled matches for a competition within the requested date window."""
    now = datetime.now(timezone.utc)
    date_to = now + timedelta(days=days)
    payload = _fetch(
        f"competitions/{league_id}/matches",
        {
            "season": season,
            "dateFrom": now.date().isoformat(),
            "dateTo": date_to.date().isoformat(),
        },
    )
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise SportsAPIError("football-data.org returned an invalid matches list.")
    fixtures = []
    for match in matches:
        if not isinstance(match, dict):
            continue
        fixture = _normalize_fixture(match, league_id)
        kickoff = _parse_datetime(fixture.get("date"))
        if (
            fixture["status"] in {"SCHEDULED", "TIMED"}
            and kickoff is not None
            and now <= kickoff <= date_to
        ):
            fixtures.append(fixture)
    return sorted(fixtures, key=lambda match: match["date"] or "")


def fetch_fixture(
    fixture_id: int, league_id: int, season: int, match_date: str
) -> dict[str, Any]:
    """Fetch one fixture and confirm that it belongs to the selected competition."""
    payload = _fetch(f"matches/{fixture_id}")
    fixture = _normalize_fixture(payload, league_id)
    if fixture["league"]["id"] != league_id or fixture["league"]["season"] != season:
        raise ValueError("Selected fixture does not match the chosen competition season.")
    return fixture


def _normalize_fixture(match: dict[str, Any], league_id: int) -> dict[str, Any]:
    home, away = match.get("homeTeam"), match.get("awayTeam")
    competition, season = match.get("competition", {}), match.get("season", {})
    competition_id = competition.get("id", league_id)
    season_year = season.get("startDate")
    season_year = (
        int(season_year[:4])
        if isinstance(season_year, str) and season_year[:4].isdigit()
        else None
    )
    if (
        not isinstance(match.get("id"), int)
        or not isinstance(home, dict)
        or not isinstance(away, dict)
        or not isinstance(home.get("id"), int)
        or not isinstance(away.get("id"), int)
        or not isinstance(home.get("name"), str)
        or not isinstance(away.get("name"), str)
    ):
        raise SportsAPIError("football-data.org returned a match with missing fields.")

    return {
        "id": match["id"],
        "date": match.get("utcDate"),
        "round": match.get("matchday"),
        "country": competition.get("area", {}).get("name"),
        "league": {
            "id": competition_id,
            "name": competition.get("name"),
            "season": season_year,
            "logo": competition.get("emblem"),
        },
        "home": {
            "id": home["id"],
            "name": home["name"],
            "logo": home.get("crest"),
        },
        "away": {
            "id": away["id"],
            "name": away["name"],
            "logo": away.get("crest"),
        },
        "status": match.get("status"),
    }


def fetch_league_standings(league_id: int, season: int) -> list[dict[str, Any]]:
    """Normalize total/home/away tables into a common team-stat structure."""
    payload = _fetch(
        f"competitions/{league_id}/standings", {"season": season}
    )
    groups = payload.get("standings")
    if not isinstance(groups, list):
        raise SportsAPIError("football-data.org returned invalid standings.")

    tables: dict[str, dict[int, dict[str, Any]]] = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("table"), list):
            continue
        table_type = group.get("type")
        if table_type not in {"TOTAL", "HOME", "AWAY"}:
            continue
        tables[table_type] = {
            row["team"]["id"]: row
            for row in group["table"]
            if isinstance(row, dict)
            and isinstance(row.get("team"), dict)
            and isinstance(row["team"].get("id"), int)
        }

    total_table = tables.get("TOTAL", {})
    if not total_table:
        raise ValueError("No current standings are available for this competition.")

    results = []
    for team_id, total in total_table.items():
        team = total["team"]
        row: dict[str, Any] = {
            "position": total.get("position"),
            "points": total.get("points"),
            "team": {
                "id": team_id,
                "name": team.get("name"),
                "logo": team.get("crest"),
            },
            "total": _normalize_record(total),
        }
        for venue, table_type in (("home", "HOME"), ("away", "AWAY")):
            venue_row = tables.get(table_type, {}).get(team_id, total)
            row[venue] = _normalize_record(venue_row)
        results.append(row)
    return results


def _normalize_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "games": row.get("playedGames", 0),
        "wins": row.get("won", 0),
        "draws": row.get("draw", 0),
        "loses": row.get("lost", 0),
        "scoredGoals": row.get("goalsFor", 0),
        "receivedGoals": row.get("goalsAgainst", 0),
    }


def calculate_league_goal_averages(
    standings: list[dict[str, Any]],
) -> dict[str, float]:
    home_goals = home_games = away_goals = away_games = 0.0
    for row in standings:
        home, away = row.get("home", {}), row.get("away", {})
        home_goals += _number(home.get("scoredGoals"))
        home_games += _number(home.get("games"))
        away_goals += _number(away.get("scoredGoals"))
        away_games += _number(away.get("games"))
    if home_games <= 0 or away_games <= 0:
        raise ValueError("Standings do not contain enough home/away goal data.")
    return {"home": home_goals / home_games, "away": away_goals / away_games}


def get_team_goal_averages(
    standings: list[dict[str, Any]], team_id: int
) -> dict[str, dict[str, float]]:
    for row in standings:
        team = row.get("team")
        if not isinstance(team, dict) or team.get("id") != team_id:
            continue
        averages: dict[str, dict[str, float]] = {}
        for venue in ("home", "away"):
            record = row.get(venue, {})
            games = _number(record.get("games"))
            if games <= 0:
                raise ValueError("Team has no completed matches in this venue.")
            averages[venue] = {
                "scored": _number(record.get("scoredGoals")) / games,
                "received": _number(record.get("receivedGoals")) / games,
                "games": games,
            }
        return averages
    raise ValueError(f"Team {team_id} is not listed in this season's standings.")


def fetch_recent_form(team_id: int) -> list[dict[str, Any]]:
    """Fetch the five most recent finished matches for a team."""
    payload = _fetch(
        f"teams/{team_id}/matches",
        {"status": "FINISHED", "limit": 5},
    )
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise SportsAPIError("football-data.org returned invalid team match history.")
    matches.sort(key=lambda match: match.get("utcDate", ""), reverse=True)
    return matches[:5]


def summarize_team_form(matches: list[dict[str, Any]], team_id: int) -> str | None:
    form: list[str] = []
    for match in matches:
        score = match.get("score", {}).get("fullTime", {})
        home_score, away_score = score.get("home"), score.get("away")
        if not isinstance(home_score, int) or not isinstance(away_score, int):
            continue
        home_team, away_team = match.get("homeTeam", {}), match.get("awayTeam", {})
        if home_team.get("id") == team_id:
            won, lost = home_score > away_score, home_score < away_score
        elif away_team.get("id") == team_id:
            won, lost = away_score > home_score, away_score < home_score
        else:
            continue
        form.append("W" if won else "L" if lost else "D")
    return "".join(form) or None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Standings contain incomplete goal statistics.") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError("Standings contain invalid goal statistics.")
    return number
