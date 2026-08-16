from datetime import date

import pytest

import ai.tools as tools
import db


def test_add_transaction_validation():
    with pytest.raises(ValueError):
        tools.add_transaction("expense", 0)

    with pytest.raises(ValueError):
        tools.add_transaction("other", 10)


def test_execute_tool_idempotency_for_mutating_tool():
    args = {
        "type_": "expense",
        "amount": 12.34,
        "category": "coffee",
        "description": "latte",
        "payment_method": "Cash",
        "txn_date": date.today().isoformat(),
    }

    first = tools.execute_tool("add_transaction", args)
    second = tools.execute_tool("add_transaction", args)

    assert first["id"] == second["id"]
    assert second.get("idempotent_replay") is True


def test_get_upcoming_unreminded_charges_marks_once(monkeypatch):
    today = date.today()
    db.add_recurring_charge("Gym", 30, today.day, payment_method="Visa")

    first = tools.get_upcoming_unreminded_charges(days_ahead=0)
    second = tools.get_upcoming_unreminded_charges(days_ahead=0)

    assert len(first) == 1
    assert first[0]["name"] == "Gym"
    assert second == []


def test_preference_set_and_get_roundtrip():
    tools.set_user_preference("default_payment", "Discover")
    pref = tools.get_user_preference("default_payment")
    assert pref == {"key": "default_payment", "value": "Discover"}
