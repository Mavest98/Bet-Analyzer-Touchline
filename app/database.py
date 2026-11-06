import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    inspect,
)
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


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id = mapped_column(Integer, primary_key=True)
    identity_subject = mapped_column(
        "google_sub", String(255), nullable=False, unique=True, index=True
    )
    firebase_uid = mapped_column(String(128), nullable=True, unique=True, index=True)
    email = mapped_column(String(320), nullable=False, index=True)
    display_name = mapped_column(String(200), nullable=False)
    picture_url = mapped_column(String(1000), nullable=True)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


class PredictionSlate(Base):
    __tablename__ = "prediction_slates"

    id = mapped_column(Integer, primary_key=True)
    user_id = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name = mapped_column(String(120), nullable=False)
    created_at = mapped_column(DateTime(timezone=True), nullable=False)


class StoredMatchPrediction(Base):
    __tablename__ = "match_predictions"

    id = mapped_column(Integer, primary_key=True, index=True)
    user_id = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slate_id = mapped_column(
        ForeignKey("prediction_slates.id", ondelete="SET NULL"), nullable=True, index=True
    )
    fixture_id = mapped_column(Integer, nullable=True, index=True)
    league_id = mapped_column(Integer, nullable=True)
    season = mapped_column(Integer, nullable=True)
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
    actual_home_goals = mapped_column(Integer, nullable=True)
    actual_away_goals = mapped_column(Integer, nullable=True)
    actual_outcome = mapped_column(String(10), nullable=True)
    settled_at = mapped_column(DateTime(timezone=True), nullable=True)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    user_columns = {
        column["name"] for column in inspect(engine).get_columns("user_accounts")
    }
    if "firebase_uid" not in user_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE user_accounts ADD COLUMN firebase_uid VARCHAR(128)"
            )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_user_accounts_firebase_uid "
            "ON user_accounts (firebase_uid)"
        )

    columns = {
        column["name"] for column in inspect(engine).get_columns("match_predictions")
    }
    if "user_id" not in columns:
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM match_predictions")

    migrations = {
        "user_id": "INTEGER REFERENCES user_accounts(id) ON DELETE CASCADE",
        "slate_id": "INTEGER REFERENCES prediction_slates(id) ON DELETE SET NULL",
        "fixture_id": "INTEGER",
        "league_id": "INTEGER",
        "season": "INTEGER",
        "actual_home_goals": "INTEGER",
        "actual_away_goals": "INTEGER",
        "actual_outcome": "VARCHAR(10)",
        "settled_at": "TIMESTAMP",
    }
    with engine.begin() as connection:
        for column, sql_type in migrations.items():
            if column not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE match_predictions ADD COLUMN {column} {sql_type}"
                )
        connection.exec_driver_sql(
            "DELETE FROM match_predictions WHERE user_id IS NULL"
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
