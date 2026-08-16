"""
ai/tools.py - Tool registry and executor for autonomous finance agent
"""

import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any, Callable

import db
from . import budget


def _today() -> str:
    return date.today().isoformat()


def _format_month_day(day: int) -> str:
    """Format day number as ordinal monthly phrase, e.g. '5th of every month'."""
    day = int(day)
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} of every month"


def get_budget_status() -> dict:
    return budget.get_budget_status()


def get_month_summary(year_month: str | None = None) -> dict:
    year_month = year_month or date.today().strftime("%Y-%m")
    return db.month_summary(year_month)


def get_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    payment_method: str | None = None,
    category: str | None = None,
    type_: str | None = None,
    limit: int = 50,
) -> list[dict]:
    txns = db.get_transactions(start_date=start_date, end_date=end_date, payment_method=payment_method)
    if category:
        txns = [t for t in txns if (t.get("category") or "").lower() == category.lower()]
    if type_ in ("income", "expense"):
        txns = [t for t in txns if t.get("type") == type_]
    return txns[: max(1, min(limit, 200))]


def get_recent_transactions(days: int = 30, type_: str | None = None, limit: int = 50) -> list[dict]:
    days = max(1, min(days, 365))
    start_date = (date.today() - timedelta(days=days)).isoformat()
    return get_transactions(start_date=start_date, end_date=_today(), type_=type_, limit=limit)


def get_active_recurring_charges() -> list[dict]:
    charges = db.get_active_recurring_charges()
    for charge in charges:
        charge["schedule"] = _format_month_day(charge["day_of_month"])
    return charges


def get_payment_methods() -> list[str]:
    return db.get_payment_methods()


def get_user_preference(key: str) -> dict:
    """Read a user preference stored in tool idempotency table namespace."""
    if not key:
        raise ValueError("key is required")
    value = db.get_tool_idempotency_result(f"pref:{key}")
    return {"key": key, "value": value}


def set_user_preference(key: str, value: Any) -> dict:
    """Set a user preference as JSON in tool idempotency table namespace."""
    if not key:
        raise ValueError("key is required")
    db.save_tool_idempotency_result(f"pref:{key}", "set_user_preference", value)
    return {"key": key, "value": value}


def add_transaction(
    type_: str,
    amount: float,
    category: str | None = None,
    description: str | None = None,
    txn_date: str | None = None,
    payment_method: str | None = None,
) -> dict:
    if type_ not in ("income", "expense"):
        raise ValueError("type_ must be 'income' or 'expense'")
    if amount is None or float(amount) <= 0:
        raise ValueError("amount must be > 0")

    txn_date = txn_date or _today()
    # Models occasionally emit placeholders like "YYYY-MM-DD"; coerce invalid dates to today.
    try:
        datetime.strptime(txn_date, "%Y-%m-%d")
    except (TypeError, ValueError):
        txn_date = _today()
    existing_same_day = db.get_transactions(start_date=txn_date, end_date=txn_date)
    similar_count = sum(
        1
        for t in existing_same_day
        if t.get("type") == type_
        and abs(float(t.get("amount") or 0.0) - float(amount)) < 0.01
        and (t.get("category") or "").lower() == (category or "").lower()
        and (t.get("payment_method") or "").lower() == (payment_method or "").lower()
    )

    txn_id = db.add_transaction(type_, float(amount), category, description, txn_date, payment_method)
    result = {"id": txn_id, "type": type_, "amount": float(amount), "payment_method": payment_method}
    if similar_count > 0:
        result["warning"] = (
            f"Found {similar_count} similar transaction(s) on {txn_date}. "
            "This entry was still added."
        )
        result["similar_existing_count"] = similar_count
    return result


def add_recurring_charge(
    name: str,
    amount: float,
    day_of_month: int,
    end_date: str | None = None,
    payment_method: str | None = None,
) -> dict:
    if not name:
        raise ValueError("name is required")
    if amount is None or float(amount) <= 0:
        raise ValueError("amount must be > 0")
    day = int(day_of_month)
    if day < 1 or day > 28:
        raise ValueError("day_of_month must be between 1 and 28")

    active = db.get_active_recurring_charges()
    normalized_name = name.strip().lower()
    similar_count = sum(
        1
        for c in active
        if (c.get("name") or "").strip().lower() == normalized_name
        and abs(float(c.get("amount") or 0.0) - float(amount)) < 0.01
        and int(c.get("day_of_month") or 0) == day
    )

    charge_id = db.add_recurring_charge(name, float(amount), day, end_date=end_date, payment_method=payment_method)
    result = {"id": charge_id, "name": name, "amount": float(amount), "day_of_month": day}
    if similar_count > 0:
        result["warning"] = (
            f"Found {similar_count} similar active recurring charge(s). "
            "This entry was still added."
        )
        result["similar_existing_count"] = similar_count
    return result


def remove_recurring_charge_by_name(name: str) -> dict:
    if not name:
        raise ValueError("name is required")
    charges = db.get_active_recurring_charges()
    needle = name.lower()
    match = next((c for c in charges if needle in (c.get("name") or "").lower()), None)
    if not match:
        return {"removed": False, "reason": f"no active recurring charge matching '{name}'"}
    db.deactivate_recurring_charge(match["id"])
    return {"removed": True, "id": match["id"], "name": match["name"]}


def get_upcoming_unreminded_charges(days_ahead: int = 3) -> list[dict]:
    days_ahead = max(0, min(int(days_ahead), 14))
    today = date.today()
    period = today.strftime("%Y-%m")
    results: list[dict] = []
    for c in db.get_active_recurring_charges():
        days_until = c["day_of_month"] - today.day
        if 0 <= days_until <= days_ahead and not db.has_reminder_been_sent(c["id"], period):
            db.mark_reminder_sent(c["id"], period)
            results.append(
                {
                    "id": c["id"],
                    "name": c["name"],
                    "amount": c["amount"],
                    "days_until": days_until,
                    "schedule": _format_month_day(c["day_of_month"]),
                    "payment_method": c.get("payment_method"),
                }
            )
    return results


TOOL_DEFS: list[dict] = [
    {
        "name": "get_budget_status",
        "description": "Get monthly AI budget utilization.",
        "args_schema": {},
    },
    {
        "name": "get_month_summary",
        "description": "Get month totals and expense by category.",
        "args_schema": {"year_month": "optional YYYY-MM"},
    },
    {
        "name": "get_transactions",
        "description": "Query transactions by date range and optional filters.",
        "args_schema": {
            "start_date": "optional YYYY-MM-DD",
            "end_date": "optional YYYY-MM-DD",
            "payment_method": "optional string",
            "category": "optional string",
            "type_": "optional 'income'|'expense'",
            "limit": "optional int, default 50",
        },
    },
    {
        "name": "get_recent_transactions",
        "description": "Get recent transactions over the last N days.",
        "args_schema": {"days": "optional int", "type_": "optional 'income'|'expense'", "limit": "optional int"},
    },
    {
        "name": "get_active_recurring_charges",
        "description": "List active recurring charges.",
        "args_schema": {},
    },
    {
        "name": "get_payment_methods",
        "description": "List known payment methods.",
        "args_schema": {},
    },
    {
        "name": "get_user_preference",
        "description": "Get a stored preference value by key.",
        "args_schema": {"key": "required string"},
    },
    {
        "name": "set_user_preference",
        "description": "Store a preference value by key.",
        "args_schema": {"key": "required string", "value": "required any JSON value"},
    },
    {
        "name": "add_transaction",
        "description": "Create an income or expense transaction.",
        "args_schema": {
            "type_": "required 'income'|'expense'",
            "amount": "required float > 0",
            "category": "optional string",
            "description": "optional string",
            "txn_date": "optional YYYY-MM-DD",
            "payment_method": "optional string",
        },
    },
    {
        "name": "add_recurring_charge",
        "description": "Create a recurring charge.",
        "args_schema": {
            "name": "required string",
            "amount": "required float > 0",
            "day_of_month": "required int 1..28",
            "end_date": "optional YYYY-MM-DD",
            "payment_method": "optional string",
        },
    },
    {
        "name": "remove_recurring_charge_by_name",
        "description": "Deactivate recurring charge by fuzzy name match.",
        "args_schema": {"name": "required string"},
    },
    {
        "name": "get_upcoming_unreminded_charges",
        "description": "Get upcoming charges and mark reminders sent for this period.",
        "args_schema": {"days_ahead": "optional int, default 3"},
    },
]


TOOL_MAP: dict[str, Callable[..., Any]] = {
    "get_budget_status": get_budget_status,
    "get_month_summary": get_month_summary,
    "get_transactions": get_transactions,
    "get_recent_transactions": get_recent_transactions,
    "get_active_recurring_charges": get_active_recurring_charges,
    "get_payment_methods": get_payment_methods,
    "get_user_preference": get_user_preference,
    "set_user_preference": set_user_preference,
    "add_transaction": add_transaction,
    "add_recurring_charge": add_recurring_charge,
    "remove_recurring_charge_by_name": remove_recurring_charge_by_name,
    "get_upcoming_unreminded_charges": get_upcoming_unreminded_charges,
}


MUTATING_TOOLS = {
    "remove_recurring_charge_by_name",
    "set_user_preference",
    "get_upcoming_unreminded_charges",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _build_idempotency_key(name: str, args: dict) -> str:
    payload = f"{name}:{_canonical_json(args)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"tool:{digest}"


def execute_tool(name: str, args: dict | None = None) -> Any:
    args = args or {}
    if name not in TOOL_MAP:
        raise ValueError(f"Unknown tool '{name}'")

    if name not in MUTATING_TOOLS:
        return TOOL_MAP[name](**args)

    key = _build_idempotency_key(name, args)
    existing = db.get_tool_idempotency_result(key)
    if existing is not None:
        if isinstance(existing, dict):
            existing["idempotent_replay"] = True
            return existing
        return {"result": existing, "idempotent_replay": True}

    result = TOOL_MAP[name](**args)
    db.save_tool_idempotency_result(key, name, result)
    return result
