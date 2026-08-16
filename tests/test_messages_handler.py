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


def test_missing_payment_method_prompts_for_follow_up(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "Spent 25 on lunch"
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_expense",
            "amount": 25,
            "category": "food",
            "description": "lunch",
            "txn_date": None,
            "payment_method": None,
        },
    )

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert "payment method" in fake_update.message.replies[-1]["text"].lower()
    assert "awaiting_payment_method" in fake_context.user_data
    pending = fake_context.user_data["awaiting_payment_method"]
    assert pending["intent"] == "log_expense"
    assert pending["amount"] == 25


def test_payment_method_follow_up_round_trip(fake_update, fake_context, monkeypatch):
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_income",
            "amount": 3000,
            "category": "income",
            "description": "salary",
            "txn_date": None,
            "payment_method": None,
        },
    )

    fake_update.message.text = "Got salary 3000"
    asyncio.run(messages.handle_message(fake_update, fake_context))
    assert "awaiting_payment_method" in fake_context.user_data

    fake_update.message.text = "Bank Account"
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})
    asyncio.run(messages.handle_message(fake_update, fake_context))

    txns = db.get_transactions()
    assert len(txns) == 1
    assert txns[0]["type"] == "income"
    assert txns[0]["payment_method"] == "Bank Account"


def test_payment_follow_up_preserves_meaningful_category_from_description(fake_update, fake_context, monkeypatch):
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_expense",
            "amount": 30,
            "category": None,
            "description": "lunch",
            "txn_date": None,
            "payment_method": None,
        },
    )
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})

    fake_update.message.text = "spent 30$ on lunch"
    asyncio.run(messages.handle_message(fake_update, fake_context))
    assert "awaiting_payment_method" in fake_context.user_data

    fake_update.message.text = "Discover"
    asyncio.run(messages.handle_message(fake_update, fake_context))

    txns = db.get_transactions()
    assert len(txns) == 1
    assert txns[0]["category"] == "lunch"
    assert "uncategorized" not in fake_update.message.replies[-1]["text"].lower()


def test_structured_income_with_payment_bypasses_agent(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "Salary credited today 4800$ to bank"
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_income",
            "amount": 4800,
            "category": "income",
            "description": "salary",
            "txn_date": None,
            "payment_method": "Bank Account",
        },
    )
    monkeypatch.setattr(messages.models, "run_agent", lambda user_input: (_ for _ in ()).throw(AssertionError("run_agent should not be called")))
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    txns = db.get_transactions()
    assert len(txns) == 1
    assert txns[0]["type"] == "income"
    assert txns[0]["amount"] == 4800
    assert "Logged income" in fake_update.message.replies[-1]["text"]


def test_model_guessed_payment_without_user_mention_still_prompts(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "salary credited 4800"
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_income",
            "amount": 4800,
            "category": "income",
            "description": "salary",
            "txn_date": None,
            "payment_method": "Bank Account",
        },
    )

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert "awaiting_payment_method" in fake_context.user_data
    assert "payment method" in fake_update.message.replies[-1]["text"].lower()


def test_explicit_payment_in_user_text_allows_direct_log(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "salary credited 4800 to bank account"
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_income",
            "amount": 4800,
            "category": "income",
            "description": "salary",
            "txn_date": None,
            "payment_method": "Bank Account",
        },
    )
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    txns = db.get_transactions()
    assert len(txns) == 1
    assert txns[0]["payment_method"] == "Bank Account"


def test_structured_expense_with_payment_bypasses_agent(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "Spent 24 on lunch with Discover"
    monkeypatch.setattr(
        messages.models,
        "classify_and_extract",
        lambda text: {
            "intent": "log_expense",
            "amount": 24,
            "category": "food",
            "description": "lunch",
            "txn_date": None,
            "payment_method": "Discover",
        },
    )
    monkeypatch.setattr(messages.models, "run_agent", lambda user_input: (_ for _ in ()).throw(AssertionError("run_agent should not be called")))
    monkeypatch.setattr(messages.budget, "get_budget_status", lambda: {"ratio": 0.0, "budget_usd": 5.0, "spend_usd": 0.0})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    txns = db.get_transactions()
    assert len(txns) == 1
    assert txns[0]["type"] == "expense"
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


def test_balance_question_does_not_trigger_recurring_list_shortcut(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "what's my balance after all my recurring charges"
    monkeypatch.setattr(messages, "route_message", lambda text: Route("finance_agent"))
    monkeypatch.setattr(messages.models, "run_agent", lambda user_input: {"ok": True, "message": "Your balance is $100.", "steps": []})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert "Your balance is $100." in fake_update.message.replies[-1]["text"]
    assert "Active recurring charges" not in fake_update.message.replies[-1]["text"]


def test_agent_reply_is_personalized(fake_update, fake_context, monkeypatch):
    fake_update.message.text = "How much did I spend this month?"
    monkeypatch.setattr(messages, "route_message", lambda text: Route("finance_agent"))
    monkeypatch.setattr(messages.models, "run_agent", lambda user_input: {"ok": True, "message": "You spent $1200 this month.", "steps": []})

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert fake_update.message.replies[-1]["text"] == "Nikhil, You spent $1200 this month."


def test_agent_failure_clears_pending_clarification(fake_update, fake_context, monkeypatch):
    fake_context.user_data["awaiting_clarification"] = {
        "original_request": "add rent",
        "question": "What amount?",
        "required_fields": ["amount"],
    }
    fake_update.message.text = "3275$"

    monkeypatch.setattr(
        messages.models,
        "run_agent",
        lambda user_input: {
            "ok": False,
            "message": "I could not complete this safely in time. Please rephrase with a bit more detail.",
            "steps": [],
        },
    )

    asyncio.run(messages.handle_message(fake_update, fake_context))

    assert "awaiting_clarification" not in fake_context.user_data


def test_new_request_drops_stale_clarification_context(fake_update, fake_context, monkeypatch):
    captured_inputs = []
    fake_context.user_data["awaiting_clarification"] = {
        "original_request": "add rent",
        "question": "What amount?",
        "required_fields": ["amount"],
    }
    fake_update.message.text = "show all recurring charges"

    def fake_run_agent(user_input: str):
        captured_inputs.append(user_input)
        return {"ok": True, "message": "Done.", "steps": []}

    monkeypatch.setattr(messages.models, "run_agent", fake_run_agent)
    monkeypatch.setattr(messages.db, "get_active_recurring_charges", lambda: [])

    asyncio.run(messages.handle_message(fake_update, fake_context))

    # Deterministic recurring-list path should handle this and clear stale context.
    assert captured_inputs == []
    assert "awaiting_clarification" not in fake_context.user_data
