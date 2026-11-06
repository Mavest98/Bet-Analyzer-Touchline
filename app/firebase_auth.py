from pathlib import Path
from typing import Any

import streamlit.components.v1 as components

_component = components.declare_component(
    "touchline_firebase_auth",
    path=str(Path(__file__).parent / "firebase_auth_component"),
)


def firebase_auth_component(config: dict[str, str]) -> dict[str, Any] | None:
    return _component(config=config, default=None, key="touchline_firebase_auth")
