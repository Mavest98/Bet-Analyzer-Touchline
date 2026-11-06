import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, mapped_column, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/soccer_analyzer"
)
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgresql://", "postgresql+psycopg2://", 1
    )
engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class StoredMatchPrediction(Base):
    __tablename__ = "match_predictions"

    id = mapped_column(Integer, primary_key=True, index=True)
    match_date = mapped_column(DateTime(timezone=True), nullable=True)
    home_team = mapped_column(String(100), nullable=False, index=True)
    away_team = mapped_column(String(100), nullable=False, index=True)
    home_xg = mapped_column(Float, nullable=False)
    away_xg = mapped_column(Float, nullable=False)
    prob_home_win = mapped_column(Float, nullable=False)
    prob_draw = mapped_column(Float, nullable=False)
    prob_away_win = mapped_column(Float, nullable=False)
    fair_home_odds = mapped_column(Float, nullable=True)
    bookie_home_odds = mapped_column(Float, nullable=True)
    ev_percentage = mapped_column(Float, nullable=True)
    is_value_bet = mapped_column(Boolean, nullable=False, default=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
