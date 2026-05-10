"""SQLite database — users, packages, reviews, config."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_DB_PATH = Path(__file__).parent.parent / "data" / "reviewer.db"


@contextmanager
def _conn():
    con = sqlite3.connect(str(_DB_PATH))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            email        TEXT    UNIQUE NOT NULL,
            password_hash TEXT   NOT NULL,
            company      TEXT    DEFAULT '',
            role         TEXT    DEFAULT 'user',
            credits      INTEGER DEFAULT 0,
            is_active    INTEGER DEFAULT 1,
            created_at   TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS packages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            description TEXT    DEFAULT '',
            credits     INTEGER NOT NULL,
            price_rm    REAL    NOT NULL,
            is_active   INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS transactions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            package_id      INTEGER,
            credits_granted INTEGER DEFAULT 0,
            amount_rm       REAL    DEFAULT 0,
            payment_status  TEXT    DEFAULT 'pending',
            note            TEXT    DEFAULT '',
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS reviews (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL,
            report_filename TEXT,
            audit_filename  TEXT,
            building_name   TEXT    DEFAULT '',
            status          TEXT    DEFAULT 'pending',
            findings_json   TEXT    DEFAULT '',
            pdf_path        TEXT    DEFAULT '',
            input_tokens    INTEGER DEFAULT 0,
            output_tokens   INTEGER DEFAULT 0,
            cost_usd        REAL    DEFAULT 0,
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS companies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            features    TEXT    DEFAULT 'seda,bei',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS bei_buildings (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            building_name    TEXT    NOT NULL UNIQUE,
            client_name      TEXT    DEFAULT '',
            address          TEXT    DEFAULT '',
            building_type    TEXT    DEFAULT '',
            year_completed   INTEGER DEFAULT 2000,
            gfa              REAL    DEFAULT 0,
            ac_pct           REAL    DEFAULT 0,
            server_area_pct  REAL    DEFAULT 0,
            parking_area_pct REAL    DEFAULT 0,
            nfa              REAL    DEFAULT 0,
            design_load_unit TEXT    DEFAULT 'pax',
            design_load      REAL    DEFAULT 0,
            actual_load_pct  REAL    DEFAULT 0,
            certifications   TEXT    DEFAULT '',
            tariff_rate_sen  REAL    DEFAULT 36.5,
            preparer_name    TEXT    DEFAULT 'Atech Energy Sdn Bhd',
            preparer_position TEXT   DEFAULT 'Energy Auditor',
            operating_hours  TEXT    DEFAULT '',
            updated_at       TEXT    DEFAULT (datetime('now'))
        );
        """)
        # Add company_id to users if upgrading from older schema
        try:
            con.execute("ALTER TABLE users ADD COLUMN company_id INTEGER DEFAULT NULL")
        except Exception:
            pass

        # Seed default internal company
        if con.execute("SELECT COUNT(*) FROM companies").fetchone()[0] == 0:
            con.execute("INSERT INTO companies(name, features) VALUES('Atech Internal','seda,bei')")

        # Seed default packages if none exist
        count = con.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
        if count == 0:
            con.executemany(
                "INSERT INTO packages (name, description, credits, price_rm) VALUES (?,?,?,?)",
                [
                    ("Starter",      "1 report review",             1,  0),
                    ("Professional", "5 report reviews",            5,  0),
                    ("Enterprise",   "20 report reviews",          20,  0),
                ],
            )


# ── Config ────────────────────────────────────────────────────────────────────

def get_config(key: str, default: str = "") -> str:
    with _conn() as con:
        row = con.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_config(key: str, value: str) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ── Users ─────────────────────────────────────────────────────────────────────

def count_users() -> int:
    with _conn() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(name: str, email: str, password_hash: str,
                company: str = "", role: str = "user") -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO users(name,email,password_hash,company,role) VALUES(?,?,?,?,?)",
            (name, email, password_hash, company, role),
        )
        return cur.lastrowid


def get_user_by_email(email: str) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_all_users() -> list[dict]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_user_credits(user_id: int, delta: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE users SET credits = MAX(0, credits + ?) WHERE id=?",
            (delta, user_id),
        )


def set_user_active(user_id: int, active: bool) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET is_active=? WHERE id=?", (int(active), user_id))


def set_user_role(user_id: int, role: str) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))


def refresh_user(user_id: int) -> dict | None:
    return get_user_by_id(user_id)


# ── Companies ─────────────────────────────────────────────────────────────────

def create_company(name: str, features: str = "seda,bei", is_active: bool = True) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO companies(name,features,is_active) VALUES(?,?,?)",
            (name, features, int(is_active)),
        )
        return cur.lastrowid


def get_all_companies() -> list[dict]:
    with _conn() as con:
        return [dict(r) for r in con.execute("SELECT * FROM companies ORDER BY name").fetchall()]


def get_company(company_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM companies WHERE id=?", (company_id,)).fetchone()
        return dict(row) if row else None


def update_company(company_id: int, name: str, features: str, is_active: bool) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE companies SET name=?,features=?,is_active=? WHERE id=?",
            (name, features, int(is_active), company_id),
        )


def set_user_company(user_id: int, company_id: int | None) -> None:
    with _conn() as con:
        con.execute("UPDATE users SET company_id=? WHERE id=?", (company_id, user_id))


def get_user_features(user_id: int) -> list[str]:
    """Return list of feature keys for a user. Admins always get all features."""
    with _conn() as con:
        user = con.execute("SELECT role, company_id FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            return []
        if user["role"] == "admin":
            return ["seda", "bei"]
        if user["company_id"]:
            row = con.execute("SELECT features FROM companies WHERE id=?",
                              (user["company_id"],)).fetchone()
            if row:
                return [f.strip() for f in row["features"].split(",") if f.strip()]
        return []


# ── Packages ──────────────────────────────────────────────────────────────────

def get_packages(active_only: bool = True) -> list[dict]:
    with _conn() as con:
        q = "SELECT * FROM packages" + (" WHERE is_active=1" if active_only else "") + " ORDER BY price_rm"
        return [dict(r) for r in con.execute(q).fetchall()]


def upsert_package(pkg_id: int | None, name: str, description: str,
                   credits: int, price_rm: float, is_active: bool = True) -> None:
    with _conn() as con:
        if pkg_id:
            con.execute(
                "UPDATE packages SET name=?,description=?,credits=?,price_rm=?,is_active=? WHERE id=?",
                (name, description, credits, price_rm, int(is_active), pkg_id),
            )
        else:
            con.execute(
                "INSERT INTO packages(name,description,credits,price_rm,is_active) VALUES(?,?,?,?,?)",
                (name, description, credits, price_rm, int(is_active)),
            )


# ── Transactions ──────────────────────────────────────────────────────────────

def create_transaction(user_id: int, credits_granted: int, amount_rm: float,
                        package_id: int | None = None,
                        status: str = "completed", note: str = "") -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO transactions(user_id,package_id,credits_granted,amount_rm,payment_status,note) "
            "VALUES(?,?,?,?,?,?)",
            (user_id, package_id, credits_granted, amount_rm, status, note),
        )


def get_all_transactions() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT t.*, u.name as user_name, u.email as user_email "
            "FROM transactions t JOIN users u ON t.user_id=u.id "
            "ORDER BY t.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── Reviews ───────────────────────────────────────────────────────────────────

def create_review(user_id: int, report_filename: str, audit_filename: str) -> int:
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO reviews(user_id,report_filename,audit_filename,status) VALUES(?,?,?,'running')",
            (user_id, report_filename, audit_filename),
        )
        return cur.lastrowid


def update_review(review_id: int, **kwargs: Any) -> None:
    allowed = {"building_name", "status", "findings_json", "pdf_path",
               "input_tokens", "output_tokens", "cost_usd"}
    fields = {k: v for k, v in kwargs.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    with _conn() as con:
        con.execute(f"UPDATE reviews SET {sets} WHERE id=?",
                    (*fields.values(), review_id))


def get_user_reviews(user_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM reviews WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_review(review_id: int) -> dict | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM reviews WHERE id=?", (review_id,)).fetchone()
        return dict(row) if row else None


def get_all_reviews() -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT r.*, u.name as user_name, u.email as user_email "
            "FROM reviews r JOIN users u ON r.user_id=u.id "
            "ORDER BY r.created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


# ── BEI Buildings ──────────────────────────────────────────────────────────────

def list_building_names() -> list[str]:
    with _conn() as con:
        rows = con.execute(
            "SELECT building_name FROM bei_buildings ORDER BY building_name"
        ).fetchall()
        return [r[0] for r in rows]


def get_building_by_name(name: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM bei_buildings WHERE building_name=?", (name,)
        ).fetchone()
        return dict(row) if row else None


def save_building_profile(profile: dict) -> None:
    cols = [
        "building_name", "client_name", "address", "building_type", "year_completed",
        "gfa", "ac_pct", "server_area_pct", "parking_area_pct", "nfa",
        "design_load_unit", "design_load", "actual_load_pct", "certifications",
        "tariff_rate_sen", "preparer_name", "preparer_position", "operating_hours",
    ]
    vals = [profile.get(c, "") for c in cols]
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
    with _conn() as con:
        con.execute(
            f"INSERT INTO bei_buildings({','.join(cols)}) "
            f"VALUES({','.join(['?'] * len(cols))}) "
            f"ON CONFLICT(building_name) DO UPDATE SET {sets}, updated_at=datetime('now')",
            vals,
        )
