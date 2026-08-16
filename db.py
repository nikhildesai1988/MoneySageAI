"""
db.py - SQLite persistence layer for MoneySage

Tables:
  transactions       - every income/expense entry you log
  recurring_charges  - subscriptions/bills that repeat monthly
  reminders_sent     - dedupe log so we don't remind you twice for the same charge/month
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "finbot.db"
DB_PATH.parent.mkdir(exist_ok=True)


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL CHECK(type IN ('income','expense')),
                amount REAL NOT NULL,
                category TEXT,
                description TEXT,
                payment_method TEXT,              -- e.g., "Discover", "Bank Account", "Cash"
                txn_date TEXT NOT NULL,           -- YYYY-MM-DD
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS recurring_charges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                day_of_month INTEGER NOT NULL,    -- 1-28 to keep it safe across months
                payment_method TEXT,              -- e.g., "Visa", "Bank Account"
                start_date TEXT NOT NULL,         -- YYYY-MM-DD
                end_date TEXT,                    -- YYYY-MM-DD or NULL = indefinite
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS reminders_sent (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                charge_id INTEGER NOT NULL REFERENCES recurring_charges(id),
                period TEXT NOT NULL,   -- YYYY-MM, one reminder per charge per month
                sent_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(charge_id, period)
            );

            CREATE TABLE IF NOT EXISTS api_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL,          -- YYYY-MM
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS tool_idempotency (
                key TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
    
    # Migration: add payment_method column if it doesn't exist
    _migrate_add_payment_method_column()


def _migrate_add_payment_method_column():
    """Add payment_method column to transactions and recurring_charges if missing."""
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            
            # Check if payment_method exists in transactions
            cur.execute("PRAGMA table_info(transactions)")
            cols = {row[1] for row in cur.fetchall()}
            if "payment_method" not in cols:
                cur.execute("ALTER TABLE transactions ADD COLUMN payment_method TEXT")
                print("✓ Added payment_method column to transactions")
            
            # Check if payment_method exists in recurring_charges
            cur.execute("PRAGMA table_info(recurring_charges)")
            cols = {row[1] for row in cur.fetchall()}
            if "payment_method" not in cols:
                cur.execute("ALTER TABLE recurring_charges ADD COLUMN payment_method TEXT")
                print("✓ Added payment_method column to recurring_charges")
    except Exception as e:
        # Migration might fail if columns already exist, which is fine
        import logging
        logging.getLogger("finbot").debug(f"Migration check: {e}")


# ---------- transactions ----------

def add_transaction(type_, amount, category, description, txn_date=None, payment_method=None):
    txn_date = txn_date or date.today().isoformat()
    import logging
    log = logging.getLogger("finbot")
    
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO transactions (type, amount, category, description, payment_method, txn_date) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (type_, amount, category, description, payment_method, txn_date),
            )
            return cur.lastrowid
        except Exception as e:
            # If payment_method column doesn't exist, try without it
            if "payment_method" in str(e):
                log.warning(f"payment_method column missing, falling back: {e}")
                cur = conn.execute(
                    "INSERT INTO transactions (type, amount, category, description, txn_date) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (type_, amount, category, description, txn_date),
                )
                return cur.lastrowid
            else:
                log.error(f"Failed to add transaction: {e}")
                raise


def get_transactions(start_date=None, end_date=None, payment_method=None):
    q = "SELECT * FROM transactions WHERE 1=1"
    params = []
    if start_date:
        q += " AND txn_date >= ?"
        params.append(start_date)
    if end_date:
        q += " AND txn_date <= ?"
        params.append(end_date)
    if payment_method:
        q += " AND payment_method = ?"
        params.append(payment_method)
    q += " ORDER BY txn_date DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def get_payment_methods():
    """Get all unique payment methods used in transactions and recurring charges."""
    with get_conn() as conn:
        methods = set()
        
        # From transactions
        rows = conn.execute(
            "SELECT DISTINCT payment_method FROM transactions WHERE payment_method IS NOT NULL"
        ).fetchall()
        methods.update(r["payment_method"] for r in rows if r["payment_method"])
        
        # From recurring_charges
        rows = conn.execute(
            "SELECT DISTINCT payment_method FROM recurring_charges WHERE payment_method IS NOT NULL AND active = 1"
        ).fetchall()
        methods.update(r["payment_method"] for r in rows if r["payment_method"])
        
        return sorted(list(methods))


# ---------- recurring charges ----------

def add_recurring_charge(name, amount, day_of_month, start_date=None, end_date=None, payment_method=None):
    start_date = start_date or date.today().isoformat()
    import logging
    log = logging.getLogger("finbot")
    
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO recurring_charges (name, amount, day_of_month, start_date, end_date, payment_method) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, amount, day_of_month, start_date, end_date, payment_method),
            )
            return cur.lastrowid
        except Exception as e:
            # If payment_method column doesn't exist, try without it
            if "payment_method" in str(e):
                log.warning(f"payment_method column missing in recurring_charges, falling back: {e}")
                cur = conn.execute(
                    "INSERT INTO recurring_charges (name, amount, day_of_month, start_date, end_date) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (name, amount, day_of_month, start_date, end_date),
                )
                return cur.lastrowid
            else:
                log.error(f"Failed to add recurring charge: {e}")
                raise


def get_active_recurring_charges(as_of=None):
    as_of = as_of or date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM recurring_charges WHERE active = 1 "
            "AND start_date <= ? AND (end_date IS NULL OR end_date >= ?)",
            (as_of, as_of),
        ).fetchall()
        return [dict(r) for r in rows]


def deactivate_recurring_charge(charge_id):
    with get_conn() as conn:
        conn.execute("UPDATE recurring_charges SET active = 0 WHERE id = ?", (charge_id,))


# ---------- reminders ----------

def has_reminder_been_sent(charge_id, period):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM reminders_sent WHERE charge_id = ? AND period = ?",
            (charge_id, period),
        ).fetchone()
        return row is not None


def mark_reminder_sent(charge_id, period):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO reminders_sent (charge_id, period) VALUES (?, ?)",
            (charge_id, period),
        )


# ---------- api usage / spend tracking ----------

def record_api_usage(input_tokens, output_tokens, cost_usd, period=None):
    period = period or date.today().strftime("%Y-%m")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO api_usage (period, input_tokens, output_tokens, cost_usd) "
            "VALUES (?, ?, ?, ?)",
            (period, input_tokens, output_tokens, cost_usd),
        )


def get_monthly_spend(period=None):
    period = period or date.today().strftime("%Y-%m")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd),0) AS spend, "
            "COALESCE(SUM(input_tokens),0) AS in_tok, "
            "COALESCE(SUM(output_tokens),0) AS out_tok "
            "FROM api_usage WHERE period = ?",
            (period,),
        ).fetchone()
        return {"spend_usd": row["spend"], "input_tokens": row["in_tok"], "output_tokens": row["out_tok"]}


# ---------- tool idempotency ----------

def get_tool_idempotency_result(key: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT result_json FROM tool_idempotency WHERE key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["result_json"])


def save_tool_idempotency_result(key: str, tool_name: str, result):
    result_json = json.dumps(result)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tool_idempotency (key, tool_name, result_json) VALUES (?, ?, ?)",
            (key, tool_name, result_json),
        )


# ---------- aggregates ----------

def month_summary(year_month):
    """year_month: 'YYYY-MM'. Returns totals for that month."""
    with get_conn() as conn:
        income = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM transactions "
            "WHERE type='income' AND txn_date LIKE ?",
            (f"{year_month}%",),
        ).fetchone()["t"]
        expense = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM transactions "
            "WHERE type='expense' AND txn_date LIKE ?",
            (f"{year_month}%",),
        ).fetchone()["t"]
        by_category = conn.execute(
            "SELECT category, SUM(amount) AS total FROM transactions "
            "WHERE type='expense' AND txn_date LIKE ? "
            "GROUP BY category ORDER BY total DESC",
            (f"{year_month}%",),
        ).fetchall()
        return {
            "income": income,
            "expense": expense,
            "net": income - expense,
            "by_category": [dict(r) for r in by_category],
        }
