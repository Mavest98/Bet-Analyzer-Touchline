import csv
import html
import io
import math
import os
from datetime import datetime, timezone
from typing import Any

import requests
import streamlit as st
from dotenv import load_dotenv
from streamlit.errors import StreamlitSecretNotFoundError

from app.firebase_auth import firebase_auth_component

load_dotenv()

API_URL = os.getenv("ANALYZER_API_URL", "http://127.0.0.1:8000").rstrip("/")
REQUEST_TIMEOUT = 30

st.set_page_config(
    page_title="Touchline | Football Intelligence",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)


def firebase_web_config() -> dict[str, str] | None:
    try:
        config = st.secrets["firebase"]
    except (KeyError, StreamlitSecretNotFoundError):
        return None
    fields = ("api_key", "auth_domain", "project_id", "app_id")
    values = {field: str(config.get(field, "")).strip() for field in fields}
    if not all(values.values()):
        return None
    server_project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    if not server_project_id or values["project_id"] != server_project_id:
        return None
    return {
        "apiKey": values["api_key"],
        "authDomain": values["auth_domain"],
        "projectId": values["project_id"],
        "appId": values["app_id"],
    }


def current_id_token() -> str | None:
    token = st.session_state.get("firebase_id_token")
    return token if isinstance(token, str) and token else None


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@400;500;600;700;800&display=swap');
    :root {
        --bg: #0a100f;
        --panel: #111a18;
        --panel-soft: #15211d;
        --line: rgba(215, 236, 226, .10);
        --text: #edf4ef;
        --muted: #91a49b;
        --lime: #c9f27a;
        --mint: #7de0b1;
        --red: #ff887d;
    }
    .stApp { background: var(--bg); color: var(--text); }
    html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
    [data-baseweb="select"] > div, input, textarea {
        background-color: #15211d !important;
        color: var(--text) !important;
        border-color: var(--line) !important;
    }
    [data-baseweb="popover"], [data-baseweb="menu"] {
        background: #15211d !important;
        color: var(--text) !important;
    }
    [role="option"] { color: var(--text) !important; }
    [data-testid="stNumberInput"] button {
        background: #20302a !important;
        color: var(--text) !important;
        border-color: var(--line) !important;
    }
    [data-testid="stTextInput"] input::placeholder { color: var(--muted) !important; }
    h1, h2, h3 { font-family: 'Manrope', sans-serif; letter-spacing: -.04em; }
    .block-container { max-width: 1480px; padding: 5rem 2rem 4rem; }
    header[data-testid="stHeader"] {
        background: rgba(10, 16, 15, .96);
        border-bottom: 1px solid var(--line);
    }
    [data-testid="stToolbar"], #MainMenu, footer { display: none !important; }
    [data-testid="stRadio"] > div[role="radiogroup"] {
        gap: 8px;
        padding: 6px;
        border: 1px solid var(--line);
        border-radius: 14px;
        background: #101815;
    }
    [data-testid="stRadio"] label {
        border-radius: 9px;
        padding: 5px 12px;
    }
    [data-testid="stSidebar"] {
        background: #0e1714;
        border-right: 1px solid var(--line);
    }
    [data-testid="stMetric"] {
        background: linear-gradient(145deg, #14201b, #101815);
        border: 1px solid var(--line);
        padding: 17px 19px;
        border-radius: 15px;
        min-height: 114px;
    }
    [data-testid="stMetricLabel"] p { color: var(--muted); font-size: .82rem; }
    [data-testid="stMetricValue"] { color: var(--text); font-family: 'Manrope', sans-serif; }
    [data-testid="stMetricDelta"] { font-size: .78rem; }
    div.stButton > button[kind="primary"] {
        background: var(--lime); border: 0; border-radius: 10px;
        color: #17200e; font-weight: 800; min-height: 45px;
    }
    div.stButton > button[kind="primary"]:hover {
        background: #d7ff8b; color: #17200e; border: 0;
    }
    div.stButton > button {
        border-radius: 10px;
        transition: all .15s ease;
    }
    [data-testid="stProgressBar"] > div > div { background: var(--lime); }
    [data-testid="stTabs"] button { font-weight: 700; }
    .masthead {
        display:flex; align-items:center; justify-content:space-between;
        gap:20px; padding: 4px 0 18px; border-bottom:1px solid var(--line);
    }
    .brand { display:flex; align-items:center; gap:12px; }
    .brand-mark {
        display:grid; place-items:center; width:42px; height:42px; border-radius:13px;
        background:var(--lime); color:#17200e; font-size:1.28rem;
    }
    .brand-name { font:800 1.05rem 'Manrope',sans-serif; letter-spacing:.025em; }
    .brand-sub { color:var(--muted); font-size:.72rem; letter-spacing:.12em; text-transform:uppercase; }
    .live-pill {
        display:inline-flex; align-items:center; gap:8px; border:1px solid var(--line);
        border-radius:999px; padding:7px 12px; color:var(--muted); font-size:.78rem;
    }
    .live-dot { width:7px; height:7px; border-radius:50%; background:var(--mint); }
    .hero {
        position:relative; overflow:hidden; padding:32px 36px; margin:24px 0 22px;
        border:1px solid var(--line); border-radius:22px;
        background:radial-gradient(ellipse at 82% 10%, rgba(201,242,122,.13), transparent 35%),
                   linear-gradient(125deg,#17251e 0%,#101a16 55%,#142119 100%);
    }
    .hero:after {
        content:'⚽'; position:absolute; right:6%; top:5%; font-size:8.5rem;
        opacity:.055; transform:rotate(-18deg); pointer-events:none;
    }
    .eyebrow {
        color:var(--lime); font-size:.7rem; font-weight:800;
        letter-spacing:.17em; text-transform:uppercase;
    }
    .hero h1 { font-size:clamp(2rem,4vw,3.15rem); margin:9px 0 8px; }
    .hero p { color:var(--muted); margin:0; max-width:670px; font-size:1rem; line-height:1.6; }
    .section-kicker {
        color:var(--muted); font-size:.72rem; font-weight:800;
        letter-spacing:.15em; text-transform:uppercase; margin:0 0 12px;
    }
    .match-card {
        border:1px solid var(--line); border-radius:18px; padding:22px;
        background:linear-gradient(145deg,#14201c,#101715);
    }
    .team-row { display:flex; align-items:center; justify-content:space-between; gap:14px; }
    .team-name { font:800 clamp(1.05rem,2vw,1.45rem) 'Manrope',sans-serif; }
    .vs {
        width:38px; height:38px; display:grid; place-items:center; border-radius:50%;
        border:1px solid var(--line); color:var(--lime); font-size:.68rem; font-weight:800;
    }
    .match-meta { margin-top:17px; color:var(--muted); font-size:.82rem; }
    .form-row { display:flex; gap:5px; margin-top:8px; }
    .form-dot {
        width:23px; height:23px; border-radius:7px; display:grid; place-items:center;
        font-size:.68rem; font-weight:800; color:#09110d;
    }
    .form-W { background:#80dbad; } .form-D { background:#e9c66e; } .form-L { background:#f28b81; }
    .score-chip {
        display:inline-flex; flex-direction:column; align-items:center; min-width:76px;
        padding:12px 10px; border:1px solid var(--line); border-radius:13px;
        background:var(--panel-soft); margin:0 7px 8px 0;
    }
    .score-chip strong { color:var(--lime); font:800 1.05rem 'Manrope',sans-serif; }
    .score-chip span { color:var(--muted); font-size:.7rem; margin-top:3px; }
    .market-card {
        background:linear-gradient(145deg,#14201b,#101815);
        border:1px solid var(--line); border-radius:14px; padding:14px 16px;
    }
    .market-card span { display:block; color:var(--muted); font-size:.77rem; }
    .market-card strong { display:block; color:var(--text); font:800 1.55rem 'Manrope',sans-serif; margin-top:5px; }
    .positive { color:var(--mint); font-weight:800; }
    .negative { color:var(--red); font-weight:800; }
    .empty-state {
        border:1px dashed rgba(215,236,226,.2); border-radius:18px;
        padding:32px; text-align:center; background:rgba(17,26,24,.55);
    }
    .empty-state h3 { margin:8px 0; }
    .empty-state p { color:var(--muted); margin:0; }
    .disclaimer { color:var(--muted); font-size:.76rem; line-height:1.5; }
    @media (max-width: 700px) {
        .block-container { padding: 4.75rem 1rem 3rem; }
        .masthead { align-items:flex-start; flex-direction:column; }
        .hero { padding:24px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def api_get(path: str, params: dict[str, Any] | None = None) -> dict:
    token = current_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.get(
        f"{API_URL}{path}",
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("The analyzer API returned an invalid response.")
    return payload


def api_post(path: str, params: dict[str, Any]) -> dict:
    token = current_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.post(
        f"{API_URL}{path}",
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("The analyzer API returned an invalid response.")
    return payload


def api_post_json(path: str, payload: dict[str, Any]) -> dict:
    token = current_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.post(
        f"{API_URL}{path}",
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("The analyzer API returned an invalid response.")
    return result


def api_delete(path: str, params: dict[str, Any] | None = None) -> dict:
    token = current_id_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = requests.delete(
        f"{API_URL}{path}",
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("The analyzer API returned an invalid response.")
    return payload


@st.cache_data(ttl=600, show_spinner=False)
def load_leagues(country_name: str, season: int) -> list[dict]:
    leagues = api_get(
        "/leagues", {"country_name": country_name, "season": season}
    ).get("leagues")
    if not isinstance(leagues, list):
        raise ValueError("The API returned an invalid league list.")
    return leagues


@st.cache_data(ttl=180, show_spinner=False)
def load_fixtures(league_id: int, season: int, days: int) -> list[dict]:
    fixtures = api_get(
        "/fixtures/upcoming",
        {"league_id": league_id, "season": season, "days": days},
    ).get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError("The API returned an invalid fixture list.")
    return fixtures


@st.cache_data(ttl=900, show_spinner=False)
def load_standings(league_id: int, season: int) -> list[dict]:
    rows = api_get(
        "/standings", {"league_id": league_id, "season": season}
    ).get("standings")
    if not isinstance(rows, list):
        raise ValueError("The API returned invalid league standings.")
    return rows


@st.cache_data(ttl=20, show_spinner=False)
def load_recent_predictions(limit: int, auth_token: str) -> list[dict]:
    token = auth_token
    response = requests.get(
        f"{API_URL}/predictions/recent",
        params={"limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("The analyzer API returned an invalid response.")
    rows = payload.get("predictions")
    if not isinstance(rows, list):
        raise ValueError("The API returned invalid prediction history.")
    return rows


def display_error(error: requests.RequestException | ValueError) -> None:
    response = error.response if isinstance(error, requests.RequestException) else None
    if response is not None:
        try:
            detail = response.json().get("detail", str(error))
        except ValueError:
            detail = str(error)
        st.error(f"Could not load live data: {detail}")
    else:
        st.error(f"Could not complete the request: {error}")


@st.dialog("Delete saved prediction")
def confirm_delete_prediction(prediction_id: int, match_label: str) -> None:
    st.warning(f"Delete **{match_label}** from your saved predictions?")
    delete_column, cancel_column = st.columns(2)
    if delete_column.button("Delete prediction", type="primary", use_container_width=True):
        try:
            api_delete(f"/predictions/{prediction_id}")
            load_recent_predictions.clear()
            st.session_state["history_notice"] = "Prediction deleted."
            st.rerun()
        except (requests.RequestException, ValueError) as error:
            display_error(error)
    if cancel_column.button("Cancel", use_container_width=True):
        st.rerun()


@st.dialog("Delete all saved predictions")
def confirm_delete_all_predictions() -> None:
    st.warning(
        "This permanently deletes every saved prediction in your workspace, "
        "including entries not currently shown. This action cannot be undone."
    )
    delete_column, cancel_column = st.columns(2)
    if delete_column.button("Delete all", type="primary", use_container_width=True):
        try:
            result = api_delete("/predictions", {"confirm": "true"})
            load_recent_predictions.clear()
            st.session_state["history_notice"] = (
                f"Deleted {result.get('count', 0)} saved predictions."
            )
            st.rerun()
        except (requests.RequestException, ValueError) as error:
            display_error(error)
    if cancel_column.button("Keep predictions", use_container_width=True):
        st.rerun()


@st.dialog("Delete prediction slate")
def confirm_delete_slate(slate_id: int, slate_name: str) -> None:
    st.warning(
        f"Delete **{slate_name}** and all predictions saved in that slate?"
    )
    delete_column, cancel_column = st.columns(2)
    if delete_column.button("Delete slate", type="primary", use_container_width=True):
        try:
            api_delete(f"/predictions/slates/{slate_id}")
            load_recent_predictions.clear()
            st.session_state["history_notice"] = "Slate and its predictions deleted."
            st.rerun()
        except (requests.RequestException, ValueError) as error:
            display_error(error)
    if cancel_column.button("Keep slate", use_container_width=True):
        st.rerun()


def parse_kickoff(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        kickoff = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if kickoff.tzinfo is None:
            return kickoff.replace(tzinfo=timezone.utc)
        return kickoff
    except ValueError:
        return None


def fixture_label(fixture: dict) -> str:
    kickoff = parse_kickoff(fixture.get("date"))
    date_label = kickoff.strftime("%a %d %b · %H:%M UTC") if kickoff else "Time TBC"
    return f"{date_label}   ·   {fixture['home']['name']}  vs  {fixture['away']['name']}"


def render_form(form: str | None) -> str:
    if not form:
        return '<span class="disclaimer">Form unavailable</span>'
    badges = "".join(
        f'<span class="form-dot form-{result}">{result}</span>'
        for result in form
        if result in {"W", "D", "L"}
    )
    return f'<div class="form-row">{badges}</div>'


def render_match_card(fixture: dict, selected_league: dict) -> None:
    kickoff = parse_kickoff(fixture.get("date"))
    kickoff_label = (
        kickoff.strftime("%A, %d %B · %H:%M UTC") if kickoff else "Kickoff TBC"
    )
    home_name = html.escape(fixture["home"]["name"])
    away_name = html.escape(fixture["away"]["name"])
    league_name = html.escape(selected_league["name"])
    st.markdown(
        f"""
        <div class="match-card">
          <div class="eyebrow">{league_name} · {kickoff_label}</div>
          <div class="team-row" style="margin-top:20px">
            <div class="team-name">{home_name}</div>
            <div class="vs">VS</div>
            <div class="team-name" style="text-align:right">{away_name}</div>
          </div>
          <div class="match-meta">{html.escape(str(fixture.get('round') or 'Upcoming fixture'))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def show_setup_message() -> None:
    st.markdown(
        """
        <div class="empty-state">
          <div class="eyebrow">YOUR MATCHDAY, AT A GLANCE</div>
          <h3>Pick a competition to get started</h3>
          <p>Choose your country and season above, explore the available leagues, then
          load a match centre to explore forecasts, form and the table.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


firebase_config = firebase_web_config()
if firebase_config is None:
    st.markdown(
        """
        <div class="hero">
          <div class="eyebrow">PRIVATE MATCHDAY WORKSPACE</div>
          <h1>Sign-in setup needed</h1>
          <p>Set up Firebase Authentication to enable private accounts, email
          verification and social sign-in.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.error(
        "Firebase web settings are missing, incomplete, or do not match the "
        "server project ID."
    )
    st.info(
        "Create a Firebase project and Web app. Enable Email/Password, Google, "
        "Microsoft, Apple and GitHub under Authentication → Sign-in method. "
        "Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` "
        "and fill in the Web app config. In `.env`, set the matching "
        "`FIREBASE_PROJECT_ID` and `GOOGLE_APPLICATION_CREDENTIALS` path to a "
        "Firebase Admin service-account JSON file; keep that file private and "
        "outside source control. Add `localhost` to Firebase's authorized domains. "
        "For Microsoft, Apple and GitHub, also enter their provider credentials "
        "in Firebase and register Firebase's callback URL with each provider."
    )
    st.stop()

if not current_id_token():
    st.markdown(
        """
        <div class="hero">
          <div class="eyebrow">TOUCHLINE · FOOTBALL INTELLIGENCE</div>
          <h1>Your matchday, your workspace.</h1>
          <p>Create an account with email or continue with a provider to build
          private match slates and track your forecasts. Predictions are estimates,
          never guarantees.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

auth_event = firebase_auth_component(firebase_config)
if isinstance(auth_event, dict):
    if auth_event.get("type") == "signed_in":
        token = auth_event.get("idToken")
        if isinstance(token, str) and token:
            st.session_state["firebase_id_token"] = token
    elif auth_event.get("type") == "signed_out":
        st.session_state.pop("firebase_id_token", None)

id_token_value = current_id_token()
if not id_token_value:
    st.stop()

try:
    account = api_get("/auth/me")
except (requests.RequestException, ValueError) as error:
    display_error(error)
    st.info("Use the account panel above to sign out and try again.")
    st.stop()

if st.session_state.get("active_account_id") not in (None, account["id"]):
    st.session_state.clear()
    st.session_state["active_account_id"] = account["id"]
    st.rerun()
st.session_state["active_account_id"] = account["id"]


with st.sidebar:
    st.markdown('<div class="brand-mark">⚽</div>', unsafe_allow_html=True)
    st.markdown("## Touchline")
    st.caption("Football intelligence, made clear.")
    st.caption(f"Signed in as {account['display_name']}")
    st.divider()
    st.markdown(
        '<div class="disclaimer">Live data by football-data.org · Probabilities are '
        'model estimates, not guarantees or betting advice.</div>',
        unsafe_allow_html=True,
    )

today = datetime.now(timezone.utc)
st.markdown(
    f"""
    <div class="masthead">
      <div class="brand">
        <div class="brand-mark">⚽</div>
        <div><div class="brand-name">TOUCHLINE</div>
        <div class="brand-sub">Football intelligence</div></div>
      </div>
      <div class="live-pill"><span class="live-dot"></span> FOOTBALL-DATA.ORG LIVE DATA
      &nbsp;·&nbsp; {today.strftime('%A, %d %B %Y')}</div>
    </div>
    <div class="hero">
      <div class="eyebrow">THE MATCHDAY DESK</div>
      <h1>Every fixture has a story.</h1>
      <p>Explore upcoming matchups, understand the numbers behind each forecast,
      and keep your competition insights together.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.markdown('<div class="section-kicker">START YOUR MATCHDAY</div>', unsafe_allow_html=True)
    competition_controls = st.columns([2, 1, 1.15], vertical_alignment="bottom")
    with competition_controls[0]:
        country_name = st.text_input(
            "Country",
            value="England",
            key="country_name_input",
        )
    with competition_controls[1]:
        season = st.number_input(
            "Season",
            min_value=2000,
            max_value=2100,
            value=2026,
            step=1,
            key="season_input",
        )
    with competition_controls[2]:
        explore_leagues = st.button(
            "Explore leagues",
            type="primary",
            use_container_width=True,
            key="explore_leagues_main",
        )

    st.caption(
        "Choose a country and season to find available competitions. Coverage depends "
        "on football-data.org; try England, Germany, Spain, Italy, France, Brazil, "
        "the Netherlands or Portugal."
    )
    if explore_leagues:
        for key in ("leagues", "fixtures", "analysis", "selected_league"):
            st.session_state.pop(key, None)
        try:
            with st.spinner("Finding available competitions…"):
                st.session_state["leagues"] = load_leagues(
                    country_name.strip(), int(season)
                )
            st.session_state["league_query"] = (country_name.strip(), int(season))
        except (requests.RequestException, ValueError) as error:
            display_error(error)

    available_leagues = st.session_state.get("leagues", [])
    league_query = st.session_state.get("league_query")
    if available_leagues and league_query == (country_name.strip(), int(season)):
        matchday_controls = st.columns([2, 1, 1.15], vertical_alignment="bottom")
        with matchday_controls[0]:
            selected_league = st.selectbox(
                "Competition",
                available_leagues,
                format_func=lambda league: league["name"],
                key="league_picker",
            )
        with matchday_controls[1]:
            horizon_label = st.selectbox(
                "Fixture window",
                ["Next 7 days", "Next 14 days", "Next 30 days"],
                index=2,
            )
            horizon_days = int(horizon_label.split()[1])
        with matchday_controls[2]:
            load_match_centre = st.button(
                "Load match centre",
                type="primary",
                use_container_width=True,
                key="load_match_centre_main",
            )
        if load_match_centre:
            for key in (
                "analysis",
                "fixtures",
                "batch_fixture_ids",
                "latest_slate_result",
            ):
                st.session_state.pop(key, None)
            try:
                with st.spinner("Loading fixtures for your competition…"):
                    st.session_state["fixtures"] = load_fixtures(
                        selected_league["id"], int(season), horizon_days
                    )
                st.session_state["fixture_query"] = (
                    selected_league["id"],
                    int(season),
                )
                st.session_state["selected_league"] = selected_league
                st.session_state["horizon_days"] = horizon_days
            except (requests.RequestException, ValueError) as error:
                display_error(error)
    elif league_query == (country_name.strip(), int(season)):
        st.info("No competitions were found for that country and season.")

fixtures = st.session_state.get("fixtures")
selected_league = st.session_state.get("selected_league")
fixture_query = st.session_state.get("fixture_query")
if fixtures is None or selected_league is None:
    if not os.getenv("FOOTBALL_DATA_API_KEY"):
        st.warning(
            "Add `FOOTBALL_DATA_API_KEY` to your ignored local `.env` file and restart the API."
        )
    show_setup_message()
    st.stop()

if fixture_query != (selected_league["id"], int(season)):
    st.info("The season or competition selection changed. Reload the match centre.")
    show_setup_message()
    st.stop()

view = st.radio(
    "Workspace",
    ["Match centre", "League table", "My predictions"],
    horizontal=True,
    label_visibility="collapsed",
)

if view == "Match centre":
    st.markdown('<div class="section-kicker">UPCOMING · MATCH CENTRE</div>', unsafe_allow_html=True)
    controls = st.columns([2, 1, 1])
    with controls[0]:
        search = st.text_input(
            "Search teams", placeholder="Search a club…", label_visibility="collapsed"
        ).strip().casefold()
    with controls[1]:
        date_filter = st.selectbox(
            "Kickoff date", ["All dates", "Today", "Next 7 days"]
        )
    favorites = st.session_state.setdefault("favorite_fixtures", set())
    with controls[2]:
        favorites_only = st.toggle("Favorites only", value=False)

    filtered_fixtures = []
    for fixture in fixtures:
        kickoff = parse_kickoff(fixture.get("date"))
        teams_match = search in (
            fixture["home"]["name"] + " " + fixture["away"]["name"]
        ).casefold()
        if search and not teams_match:
            continue
        if favorites_only and fixture["id"] not in favorites:
            continue
        if date_filter == "Today" and (kickoff is None or kickoff.date() != today.date()):
            continue
        if date_filter == "Next 7 days" and (
            kickoff is None or (kickoff - today).days > 7
        ):
            continue
        filtered_fixtures.append(fixture)

    summary = st.columns(3)
    summary[0].metric("Competition", selected_league["name"])
    summary[1].metric("Upcoming fixtures", len(fixtures))
    summary[2].metric("Matches shown", len(filtered_fixtures))

    if not filtered_fixtures:
        st.info("No fixtures match those filters. Try another date or clear the search.")
    else:
        fixture_by_id = {fixture["id"]: fixture for fixture in filtered_fixtures}
        st.markdown("#### Build a matchday slate")
        st.caption(
            "Select up to 40 fixtures from this competition. Every pick is forecast "
            "independently; combined accumulator probabilities are not implied."
        )
        selected_fixture_ids = st.multiselect(
            "Fixtures for this slate",
            options=list(fixture_by_id),
            format_func=lambda fixture_id: fixture_label(
                fixture_by_id[fixture_id]
            ),
            max_selections=40,
            key="batch_fixture_ids",
        )
        slate_odds: dict[int, float | None] = {}
        if selected_fixture_ids:
            st.caption(
                "Optionally enter the home-win decimal odds shown by your bookmaker. "
                "Leave a price blank if you do not want an edge estimate."
            )
            odds_rows = [
                {
                    "Fixture ID": fixture_id,
                    "Fixture": fixture_label(fixture_by_id[fixture_id]),
                    "Home odds": None,
                }
                for fixture_id in selected_fixture_ids
            ]
            odds_editor_key = "slate_odds_" + "_".join(
                str(fixture_id) for fixture_id in sorted(selected_fixture_ids)
            )
            edited_odds = st.data_editor(
                odds_rows,
                hide_index=True,
                disabled=["Fixture ID", "Fixture"],
                column_config={
                    "Fixture ID": None,
                    "Fixture": st.column_config.TextColumn("Fixture"),
                    "Home odds": st.column_config.NumberColumn(
                        "Home odds",
                        min_value=1.01,
                        max_value=100.0,
                        step=0.01,
                        format="%.2f",
                    ),
                },
                key=odds_editor_key,
                use_container_width=True,
            )
            slate_odds = {
                int(row["Fixture ID"]): (
                    float(row["Home odds"])
                    if row.get("Home odds") is not None
                    and math.isfinite(float(row["Home odds"]))
                    else None
                )
                for row in edited_odds.to_dict("records")
            }
        slate_controls = st.columns([2, 1])
        with slate_controls[0]:
            slate_name = st.text_input(
                "Slate name",
                value=f"{selected_league['name']} · {today:%d %b}",
                max_chars=120,
                key="new_slate_name",
            )
        with slate_controls[1]:
            if st.button(
                f"Forecast {len(selected_fixture_ids)} fixtures",
                type="primary",
                disabled=not selected_fixture_ids,
                use_container_width=True,
                key="create_prediction_slate",
            ):
                payload = {
                    "name": slate_name.strip() or "Matchday slate",
                    "fixtures": [
                        {
                            "fixture_id": fixture_id,
                            "league_id": selected_league["id"],
                            "season": int(season),
                            "match_date": fixture_by_id[fixture_id]["date"][:10],
                            "bookie_home_odds": slate_odds.get(fixture_id),
                        }
                        for fixture_id in selected_fixture_ids
                    ],
                }
                try:
                    with st.spinner(
                        f"Building {len(selected_fixture_ids)} forecasts… "
                        "Provider limits may affect large batches."
                    ):
                        st.session_state["latest_slate_result"] = api_post_json(
                            "/predictions/slates", payload
                        )
                    load_recent_predictions.clear()
                except (requests.RequestException, ValueError) as error:
                    display_error(error)

        slate_result = st.session_state.get("latest_slate_result")
        if slate_result:
            slate = slate_result.get("slate")
            if slate:
                st.success(
                    f"Saved **{slate['prediction_count']}** predictions to "
                    f"**{slate['name']}**."
                )
                st.dataframe(
                    [
                        {
                            "Fixture": prediction["match"],
                            "Home xG": f"{prediction['expected_goals']['home_xg']:.2f}",
                            "Away xG": f"{prediction['expected_goals']['away_xg']:.2f}",
                            "Home win": f"{prediction['probabilities']['home_win']:.1%}",
                            "Draw": f"{prediction['probabilities']['draw']:.1%}",
                            "Away win": f"{prediction['probabilities']['away_win']:.1%}",
                            "Fair home odds": (
                                f"{prediction['calculated_fair_odds']['home_win']:.2f}"
                                if prediction["calculated_fair_odds"]["home_win"] is not None
                                else "—"
                            ),
                            "Book odds": (
                                f"{prediction['value_analysis']['bookie_home_odds']:.2f}"
                                if prediction["value_analysis"]["bookie_home_odds"]
                                is not None
                                else "—"
                            ),
                            "Home EV": (
                                f"{prediction['value_analysis']['ev_percentage']:+.1f}%"
                                if prediction["value_analysis"]["ev_percentage"]
                                is not None
                                else "—"
                            ),
                        }
                        for prediction in slate_result["predictions"]
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            failures = slate_result.get("failures", [])
            if failures:
                st.warning(
                    f"{len(failures)} fixture(s) could not be forecast. "
                    "Successful picks were still saved."
                )
                st.dataframe(failures, hide_index=True, use_container_width=True)

        selected_fixture = st.selectbox(
            "Choose a fixture to explore",
            filtered_fixtures,
            format_func=fixture_label,
            key="fixture_picker",
        )
        render_match_card(selected_fixture, selected_league)
        is_favorite = selected_fixture["id"] in favorites
        favorite_column, note_column = st.columns([1, 3], vertical_alignment="center")
        if favorite_column.button(
            "★  Saved to shortlist" if is_favorite else "☆  Add to shortlist",
            key=f"favorite-{selected_fixture['id']}",
        ):
            if is_favorite:
                favorites.remove(selected_fixture["id"])
            else:
                favorites.add(selected_fixture["id"])
            st.rerun()
        note_column.caption(
            f"{len(favorites)} fixture{'s' if len(favorites) != 1 else ''} in your session shortlist"
        )

        st.markdown("#### Forecast setup")
        odds_column, action_column = st.columns([1, 2], vertical_alignment="bottom")
        with odds_column:
            bookmaker_odds = st.number_input(
                "Home win decimal odds · optional",
                min_value=1.01,
                max_value=100.0,
                value=None,
                step=0.01,
                help="Compare your price with model fair odds to estimate expected value.",
            )
        with action_column:
            analyze = st.button(
                "Build full match forecast",
                type="primary",
                use_container_width=True,
            )

        if analyze:
            params: dict[str, Any] = {
                "fixture_id": selected_fixture["id"],
                "league_id": selected_league["id"],
                "season": int(season),
                "match_date": selected_fixture["date"][:10],
            }
            if bookmaker_odds is not None:
                params["bookie_home_odds"] = bookmaker_odds
            try:
                with st.spinner("Analysing season data and recent form…"):
                    st.session_state["analysis"] = api_post(
                        "/analyze/fixture", params
                    )
                st.session_state["analysis_fixture_id"] = selected_fixture["id"]
            except (requests.RequestException, ValueError) as error:
                display_error(error)

        analysis = st.session_state.get("analysis")
        if (
            analysis
            and st.session_state.get("analysis_fixture_id") == selected_fixture["id"]
        ):
            st.divider()
            st.markdown('<div class="section-kicker">THE MODEL VIEW</div>', unsafe_allow_html=True)
            st.caption(
                f"Built from {analysis['data_source']} · "
                f"{selected_league['name']} {season}"
            )

            home_form = analysis.get("recent_form", {}).get("home")
            away_form = analysis.get("recent_form", {}).get("away")
            xg_cards = st.columns([1, 1, 1.3])
            xg_cards[0].metric(
                f"{selected_fixture['home']['name']} · xG",
                f"{analysis['expected_goals']['home_xg']:.2f}",
                f"Recent form {home_form or 'N/A'}",
            )
            xg_cards[1].metric(
                f"{selected_fixture['away']['name']} · xG",
                f"{analysis['expected_goals']['away_xg']:.2f}",
                f"Recent form {away_form or 'N/A'}",
            )
            edge = analysis["value_analysis"]
            if edge["ev_percentage"] is None:
                xg_cards[2].metric("Price check", "Add odds to compare")
            else:
                xg_cards[2].metric(
                    "Model edge · home win",
                    f"{edge['ev_percentage']:+.2f}%",
                    "Above +5% threshold"
                    if edge["is_value_bet"]
                    else "Below +5% threshold",
                    delta_color="normal",
                )

            st.markdown("#### Match outcome")
            outcome_columns = st.columns(3)
            outcome_labels = (
                ("home_win", selected_fixture["home"]["name"], "home_win"),
                ("draw", "Draw", "draw"),
                ("away_win", selected_fixture["away"]["name"], "away_win"),
            )
            for column, (key, label, odds_key) in zip(
                outcome_columns, outcome_labels
            ):
                probability = analysis["probabilities"][key]
                fair_odds = analysis["calculated_fair_odds"][odds_key]
                column.metric(
                    label,
                    f"{probability * 100:.1f}%",
                    f"Model fair price · {fair_odds:.2f}"
                    if fair_odds is not None
                    else "Fair price unavailable",
                )
                column.progress(min(max(probability, 0.0), 1.0))

            market_columns = st.columns(2)
            with market_columns[0]:
                st.markdown("#### Goal lines")
                totals = analysis["goal_markets"]
                st.dataframe(
                    [
                        {
                            "Market": f"Over {line} goals",
                            "Probability": f"{totals[f'over_{key}'] * 100:.1f}%",
                        }
                        for line, key in (
                            ("1.5", "1_5"),
                            ("2.5", "2_5"),
                            ("3.5", "3_5"),
                        )
                    ]
                    + [
                        {
                            "Market": f"Under {line} goals",
                            "Probability": f"{totals[f'under_{key}'] * 100:.1f}%",
                        }
                        for line, key in (
                            ("1.5", "1_5"),
                            ("2.5", "2_5"),
                            ("3.5", "3_5"),
                        )
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
            with market_columns[1]:
                st.markdown("#### Both teams to score")
                btts = analysis["both_teams_to_score"]
                btts_yes, btts_no = st.columns(2)
                btts_yes.markdown(
                    '<div class="market-card"><span>Yes</span><strong>'
                    + f"{btts['yes'] * 100:.1f}%"
                    + "</strong></div>",
                    unsafe_allow_html=True,
                )
                btts_no.markdown(
                    '<div class="market-card"><span>No</span><strong>'
                    + f"{btts['no'] * 100:.1f}%"
                    + "</strong></div>",
                    unsafe_allow_html=True,
                )
                st.markdown("#### Most likely scorelines")
                scoreline_html = "".join(
                    '<div class="score-chip"><strong>'
                    + html.escape(scoreline["score"])
                    + "</strong><span>"
                    + f"{scoreline['probability'] * 100:.1f}%"
                    + "</span></div>"
                    for scoreline in analysis["top_scorelines"]
                )
                st.markdown(scoreline_html, unsafe_allow_html=True)

            with st.expander("How this forecast is built"):
                team_data = analysis["team_goal_averages"]
                league_data = analysis["league_averages"]
                st.markdown(
                    "Team home/away goals scored and conceded are compared with "
                    "the competition's venue-specific averages. Independent Poisson "
                    "goal distributions produce the outcome and totals probabilities."
                )
                detail_columns = st.columns(3)
                detail_columns[0].write(
                    {
                        "League home goals / match": league_data["home_goals"],
                        "League away goals / match": league_data["away_goals"],
                    }
                )
                detail_columns[1].write(
                    {
                        "Home team home goals / match": team_data["home"]["home"]["scored"],
                        "Home team home conceded / match": team_data["home"]["home"]["received"],
                    }
                )
                detail_columns[2].write(
                    {
                        "Away team away goals / match": team_data["away"]["away"]["scored"],
                        "Away team away conceded / match": team_data["away"]["away"]["received"],
                    }
                )
                st.caption(
                    "Predictions are statistical estimates. They do not account for "
                    "lineups, injuries, odds movement, or other unmodelled factors."
                )

elif view == "League table":
    st.markdown('<div class="section-kicker">COMPETITION · SEASON TABLE</div>', unsafe_allow_html=True)
    try:
        with st.spinner("Loading current standings…"):
            table = load_standings(selected_league["id"], int(season))
        if not table:
            st.info("No standings are available yet for this competition and season.")
        else:
            st.markdown(f"### {selected_league['name']} · {season}")
            st.dataframe(
                [
                    {
                        "#": row["position"],
                        "Club": row["team"],
                        "P": row["played"],
                        "W": row["wins"],
                        "D": row["draws"],
                        "L": row["losses"],
                        "GF": row["goals_for"],
                        "GA": row["goals_against"],
                        "GD": row["goal_difference"],
                        "Pts": row["points"],
                    }
                    for row in table
                ],
                hide_index=True,
                use_container_width=True,
            )
            st.caption("Standings supplied by football-data.org; availability depends on competition coverage.")
    except (requests.RequestException, ValueError) as error:
        display_error(error)

else:
    st.markdown('<div class="section-kicker">YOUR WORKSPACE · RECENT FORECASTS</div>', unsafe_allow_html=True)
    notice = st.session_state.pop("history_notice", None)
    if notice:
        st.success(notice)
    try:
        history = load_recent_predictions(200, id_token_value)
        slates = api_get("/predictions/slates", {"limit": 100}).get("slates")
        performance = api_get("/predictions/performance")
        if not isinstance(slates, list):
            raise ValueError("The API returned invalid saved slates.")
        pending_count = performance["eligible_for_settlement"]
        history_controls = st.columns([1.35, 1, 1, 1.15])
        history_controls[0].metric(
            "1X2 top-pick accuracy",
            (
                f"{performance['accuracy']:.1%}"
                if performance["accuracy"] is not None
                else "Not enough results"
            ),
            f"{performance['correct_outcomes']} correct",
        )
        history_controls[1].metric(
            "Results tracked",
            performance["settled_count"],
            f"{performance['pending_results']} pending",
        )
        history_controls[2].metric(
            "Mean Brier score",
            (
                f"{performance['mean_brier_score']:.3f}"
                if performance["mean_brier_score"] is not None
                else "—"
            ),
            "Lower is better",
        )
        if history_controls[3].button(
            "Check finished results",
            disabled=pending_count == 0,
            use_container_width=True,
        ):
            try:
                report = api_post("/predictions/results/refresh", {"limit": 15})
                st.session_state["result_refresh_report"] = report
                load_recent_predictions.clear()
                st.rerun()
            except (requests.RequestException, ValueError) as error:
                display_error(error)

        result_report = st.session_state.pop("result_refresh_report", None)
        if result_report:
            st.success(
                f"Updated {result_report['updated_count']} finished results. "
                f"{result_report['pending_count']} eligible predictions remain unchecked."
            )
            if result_report["failures"]:
                st.warning(
                    f"{len(result_report['failures'])} result lookup(s) failed; "
                    "you can retry later."
                )

        if slates:
            slate_controls = st.columns([2, 1])
            with slate_controls[0]:
                selected_slate = st.selectbox(
                    "Saved slate",
                    [None, *slates],
                    format_func=lambda slate: (
                        "All predictions"
                        if slate is None
                        else f"{slate['name']} · {slate['prediction_count']} picks"
                    ),
                    key="history_slate_filter",
                )
            with slate_controls[1]:
                slate_to_delete = st.selectbox(
                    "Slate actions",
                    slates,
                    format_func=lambda slate: slate["name"],
                    key="history_slate_actions",
                )
                if st.button("Delete slate"):
                    confirm_delete_slate(
                        slate_to_delete["id"], slate_to_delete["name"]
                    )
        else:
            selected_slate = None

        if not history:
            st.markdown(
                """
                <div class="empty-state">
                  <div class="eyebrow">YOUR WORKSPACE</div>
                  <h3>No saved predictions yet</h3>
                  <p>Run a fixture forecast in the Match centre and it will appear here.</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            filter_columns = st.columns([2, 1, 1])
            with filter_columns[0]:
                prediction_search = st.text_input(
                    "Search saved matches",
                    placeholder="Search a club…",
                    label_visibility="collapsed",
                ).strip().casefold()
            with filter_columns[1]:
                value_only = st.toggle("Value picks only")
            with filter_columns[2]:
                if st.button("Refresh history"):
                    load_recent_predictions.clear()
                    st.rerun()

            visible_history = [
                row
                for row in history
                if (
                    selected_slate is None
                    or row["slate_id"] == selected_slate["id"]
                )
                if (
                    not prediction_search
                    or prediction_search
                    in f"{row['home_team']} {row['away_team']}".casefold()
                )
                and (not value_only or row["is_value_bet"])
            ]
            if not visible_history:
                st.info("No saved predictions match those filters.")
            else:
                value_bets = [
                    row for row in visible_history if row["is_value_bet"]
                ]
                avg_home_prob = sum(
                    row["prob_home_win"] for row in visible_history
                ) / len(visible_history)
                metric_columns = st.columns(3)
                metric_columns[0].metric("Forecasts shown", len(visible_history))
                metric_columns[1].metric("Above value threshold", len(value_bets))
                metric_columns[2].metric(
                    "Average home probability", f"{avg_home_prob:.1%}"
                )
                st.dataframe(
                    [
                        {
                            "ID": row["id"],
                            "Slate": row["slate_id"] or "Single forecast",
                            "Match": f"{row['home_team']} vs {row['away_team']}",
                            "Kickoff": (
                                row["match_date"][:16].replace("T", " ")
                                if row["match_date"]
                                else "—"
                            ),
                            "xG": f"{row['home_xg']:.2f} – {row['away_xg']:.2f}",
                            "Home": f"{row['prob_home_win']:.1%}",
                            "Draw": f"{row['prob_draw']:.1%}",
                            "Away": f"{row['prob_away_win']:.1%}",
                            "Book price": (
                                f"{row['bookie_home_odds']:.2f}"
                                if row["bookie_home_odds"]
                                else "—"
                            ),
                            "EV": (
                                f"{row['ev_percentage']:+.2f}%"
                                if row["ev_percentage"] is not None
                                else "—"
                            ),
                            "Value": "Yes" if row["is_value_bet"] else "—",
                            "Result": (
                                f"{row['actual_home_goals']}–{row['actual_away_goals']}"
                                if row["actual_outcome"] is not None
                                else "Pending"
                                if row["fixture_id"] is not None
                                else "—"
                            ),
                        }
                        for row in visible_history
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
                csv_buffer = io.StringIO()
                export_fields = [
                    "id",
                    "slate_id",
                    "match_date",
                    "home_team",
                    "away_team",
                    "home_xg",
                    "away_xg",
                    "prob_home_win",
                    "prob_draw",
                    "prob_away_win",
                    "bookie_home_odds",
                    "ev_percentage",
                    "is_value_bet",
                    "actual_home_goals",
                    "actual_away_goals",
                    "actual_outcome",
                ]
                writer = csv.DictWriter(csv_buffer, fieldnames=export_fields)
                writer.writeheader()
                writer.writerows(
                    {field: row.get(field) for field in export_fields}
                    for row in visible_history
                )
                action_columns = st.columns([1, 1, 1])
                with action_columns[0]:
                    selected_prediction = st.selectbox(
                        "Prediction to delete",
                        visible_history,
                        format_func=lambda row: (
                            f"#{row['id']} · {row['home_team']} vs {row['away_team']}"
                        ),
                        key="prediction_to_delete",
                    )
                    if st.button("Delete selected", key="delete_one_prediction"):
                        confirm_delete_prediction(
                            selected_prediction["id"],
                            f"{selected_prediction['home_team']} vs "
                            f"{selected_prediction['away_team']}",
                        )
                with action_columns[1]:
                    st.download_button(
                        "Export visible predictions",
                        data=csv_buffer.getvalue(),
                        file_name="touchline-predictions.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                with action_columns[2]:
                    if st.button(
                        "Delete all history",
                        key="delete_all_predictions",
                        type="secondary",
                    ):
                        confirm_delete_all_predictions()
    except (requests.RequestException, ValueError) as error:
        display_error(error)

st.divider()
st.markdown(
    '<div class="disclaimer">Touchline is an informational match-analysis tool. '
    'Forecasts are estimates only and are not guarantees of match outcomes or '
    'financial returns.</div>',
    unsafe_allow_html=True,
)
