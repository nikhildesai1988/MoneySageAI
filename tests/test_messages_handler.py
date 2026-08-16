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

    assert fake_update.message.replies[-1]["text"] == "Finance only"


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
