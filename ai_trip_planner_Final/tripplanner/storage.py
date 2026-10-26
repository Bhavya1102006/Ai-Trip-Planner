"""Saving trips. SQLite is the default (standard library); PostgreSQL is used when
DATABASE_URL starts with postgres:// or postgresql:// (Render, Supabase, Neon, ...).

Only the traveller's own brief and the generated plan are stored. Place data from
Google is not stored (see places.py). All queries are parameterised.
"""
import datetime
import json
import sqlite3
from typing import List, Optional

from .data_loader import fmt_time
from .errors import CloudServiceError
from .models import TripBrief


def itinerary_to_dict(it) -> dict:
    b: TripBrief = it.brief
    return {
        "brief": {"destination": b.destination, "days": b.days, "budget_per_day": b.budget_per_day,
                  "interests": b.interests, "pace": b.pace},
        "days": [{"day": d.day, "km": d.distance_km, "cost": d.cost,
                  "stops": [{"id": s.poi.id, "name": s.poi.name, "arrive": fmt_time(s.arrive),
                             "depart": fmt_time(s.depart), "cost": s.poi.cost,
                             "travel_km": s.travel_from_prev_km, "local_pick": not s.poi.mainstream}
                            for s in d.stops]} for d in it.days],
        "unscheduled": it.unscheduled,
        "total_km": it.total_km,
    }


class SQLiteStore:
    placeholder = "?"
    name = "sqlite"

    def __init__(self, path: str = "trips.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("""CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
            brief_text TEXT NOT NULL, plan_json TEXT NOT NULL)""")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_user ON trips(user_id)")
        self._conn.commit()

    def _now(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def save_trip(self, user_id: str, brief_text: str, plan: dict) -> int:
        cur = self._conn.execute(
            "INSERT INTO trips (user_id, created_at, brief_text, plan_json) VALUES (?, ?, ?, ?)",
            (user_id, self._now(), brief_text, json.dumps(plan)))
        self._conn.commit()
        return cur.lastrowid

    def get_trip(self, trip_id: int, user_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT id, created_at, brief_text, plan_json FROM trips WHERE id = ? AND user_id = ?",
            (trip_id, user_id)).fetchone()
        return self._row(row)

    def list_trips(self, user_id: str, limit: int = 20) -> List[dict]:
        rows = self._conn.execute(
            "SELECT id, created_at, brief_text, plan_json FROM trips WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
        return [self._row(r) for r in rows]

    @staticmethod
    def _row(row) -> Optional[dict]:
        if row is None:
            return None
        plan = row[3] if isinstance(row[3], dict) else json.loads(row[3])
        return {"id": row[0], "created_at": str(row[1]), "brief": row[2], "plan": plan}


class PostgresStore(SQLiteStore):
    """Same interface on PostgreSQL. Needs: pip install "psycopg[binary]".
    Not exercised by the automated tests (they have no database server); try it against
    your hosted database with scripts/check_cloud.py before relying on it."""
    name = "postgres"

    def __init__(self, url: str):
        try:
            import psycopg
        except ImportError as exc:
            raise CloudServiceError('PostgreSQL support needs: pip install "psycopg[binary]"') from exc
        try:
            self._conn = psycopg.connect(url, autocommit=True)
            self._conn.execute("""CREATE TABLE IF NOT EXISTS trips (
                id SERIAL PRIMARY KEY, user_id TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                brief_text TEXT NOT NULL, plan_json JSONB NOT NULL)""")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_user ON trips(user_id)")
        except psycopg.Error as exc:
            raise CloudServiceError(f"Could not connect to the database ({type(exc).__name__}).") from exc

    def save_trip(self, user_id, brief_text, plan):
        cur = self._conn.execute(
            "INSERT INTO trips (user_id, brief_text, plan_json) VALUES (%s, %s, %s::jsonb) RETURNING id",
            (user_id, brief_text, json.dumps(plan)))
        return cur.fetchone()[0]

    def get_trip(self, trip_id, user_id):
        row = self._conn.execute(
            "SELECT id, created_at, brief_text, plan_json FROM trips WHERE id = %s AND user_id = %s",
            (trip_id, user_id)).fetchone()
        return self._row(row)

    def list_trips(self, user_id, limit=20):
        rows = self._conn.execute(
            "SELECT id, created_at, brief_text, plan_json FROM trips WHERE user_id = %s "
            "ORDER BY id DESC LIMIT %s", (user_id, limit)).fetchall()
        return [self._row(r) for r in rows]


def open_store(database_url: str):
    if database_url.startswith(("postgres://", "postgresql://")):
        return PostgresStore(database_url)
    if database_url.startswith("sqlite:///"):
        return SQLiteStore(database_url[len("sqlite:///"):] or "trips.db")
    raise CloudServiceError("DATABASE_URL must start with sqlite:///, postgres:// or postgresql://")
