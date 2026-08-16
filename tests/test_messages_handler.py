import asyncio
from types import SimpleNamespace

import db
from handlers import messages


class Route:
    def __init__(self, mode: str, reply: str | None = None):
        self.mode = mode
        self.reply = reply


def test_handle_message_greeting_returns_help(fake_update, fake_context):
    fake_update.message.text = "hello"

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert fake_update.message.replies
    assert "Hi Nikhil." in fake_update.message.replies[-1]["text"]
    assert "MoneySage" in fake_update.message.replies[-1]["text"]


def test_owner_only_blocks_non_owner(fake_context):
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=999, username="stranger"),
        message=SimpleNamespace(text="hello", replies=[], reply_text=lambda *a, **k: None),
    )

    async def _fake_reply_text(*args, **kwargs):
        return None

    update.message.reply_text = _fake_reply_text
    asyncio.run(messages.handle_message(update, fake_context))


def test_handle_message_redirects_out_of_scope(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "tell me a joke"
    monkeypatch.setattr(messages, "route_message", lambda text: Route("decline", "Finance only"))

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert fake_update.message.replies[-1]["text"] == "Nikhil, Finance only"


def test_handle_message_awaiting_payment_method_logs_expense(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "Discover"
    fake_context.user_data["awaiting_payment_method"] = {
        "intent": "log_expense",
        "amount": 25.0,
        "category": "food",
        "description": "lunch",
        "txn_date": None,
    }
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    txns = db.get_transactions()
    assert len(txns) == 1
    assert txns[0]["payment_method"] == "Discover"
    assert "Logged expense" in fake_update.message.replies[-1]["text"]


def test_clarification_follow_up_numeric_amount_resumes_agent(fake_update, fake_context, monkeypatch):
    calls = []

    def fake_run_agent(user_input: str):
        calls.append(user_input)
        if len(calls) == 1:
            return {
                "ok": True,
                "message": "What amount should I use for monthly rent?",
                "needs_clarification": True,
                "required_fields": ["amount"],
                "steps": [],
            }
        return {"ok": True, "message": "Added recurring rent: $3275/month", "steps": []}

    monkeypatch.setattr(messages.models, "run_agent", fake_run_agent)
    monkeypatch.setattr(
        messages,
        "route_message",
        lambda text: Route("finance_agent") if "rent" in text.lower() else Route(
            "decline", "I keep this chat focused on personal finance so advice stays useful and grounded."
        ),
    )
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})

    # First turn asks for clarification.
    fake_update.message.text = "add monthly rent expense"
    asyncio.run(messages.handle_message(fake_update, fake_context))
    assert "amount" in fake_update.message.replies[-1]["text"].lower()
    assert "awaiting_clarification" in fake_context.user_data

    # Follow-up is a short numeric answer that would normally fail guardrails.
    fake_update.message.text = "3275$"
    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert "Added recurring rent" in fake_update.message.replies[-1]["text"]
    assert len(calls) == 2
    assert "Original request:" in calls[1]
    assert "User clarification: 3275$" in calls[1]


def test_natural_language_list_recurring_returns_all_active(fake_update, fake_context):
    db.add_recurring_charge("Rent", 3275, 1, payment_method="Bank")
    db.add_recurring_charge("Gym", 65, 15, payment_method="Card")
    fake_update.message.text = "show all recurring charges"

    asyncio.run(messages.handle_message(fake_update, fake_context))

    text = fake_update.message.replies[-1]["text"]
    assert "Active recurring charges" in text
    assert "Rent" in text
    assert "Gym" in text


def test_natural_language_list_recurring_empty_state(fake_update, fake_context):
    fake_update.message.text = "list my subscriptions"

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert fake_update.message.replies[-1]["text"] == "Nikhil, No active recurring charges."


def test_agent_reply_is_personalized(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "How much did I spend this month?"
    monkeypatch.setattr(messages, "route_message", lambda text: Route("finance_agent"))
    monkeypatch.setattr(messages.models, "run_agent", lambda user_input: {"ok": True, "message": "You spent $1200 this month.", "steps": []})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert fake_update.message.replies[-1]["text"] == "Nikhil, You spent $1200 this month."
