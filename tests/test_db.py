from datetime import date

import db


def test_add_and_get_transaction_roundtrip():
    txn_id = db.add_transaction(
        "expense", 42.5, "groceries", "milk", date.today().isoformat(), payment_method="Visa"
    )

    rows = db.get_transactions()
    assert rows
    assert rows[0]["id"] == txn_id
    assert rows[0]["type"] == "expense"
    assert rows[0]["amount"] == 42.5
    assert rows[0]["payment_method"] == "Visa"


def test_month_summary_aggregates_income_expense_and_categories():
    today = date.today()
    ym = today.strftime("%Y-%m")
    d = today.isoformat()

    db.add_transaction("income", 2000, "salary", "paycheck", d, payment_method="Bank")
    db.add_transaction("expense", 50, "groceries", "food", d, payment_method="Card")
    db.add_transaction("expense", 20, "transport", "bus", d, payment_method="Card")

    summary = db.month_summary(ym)

    assert summary["income"] == 2000
    assert summary["expense"] == 70
    assert summary["net"] == 1930
    cats = {c["category"]: c["total"] for c in summary["by_category"]}
    assert cats["groceries"] == 50
    assert cats["transport"] == 20


def test_reminder_sent_deduplication():
    charge_id = db.add_recurring_charge("Netflix", 15, 20, payment_method="Visa")
    period = date.today().strftime("%Y-%m")

    assert db.has_reminder_been_sent(charge_id, period) is False
    db.mark_reminder_sent(charge_id, period)
    assert db.has_reminder_been_sent(charge_id, period) is True
