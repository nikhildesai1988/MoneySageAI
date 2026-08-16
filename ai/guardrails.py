"""
ai/guardrails.py - Policy router for finance-focused chat guardrails
"""

from dataclasses import dataclass


@dataclass
class RouteDecision:
    mode: str  # finance_agent | empathetic_redirect | decline
    category: str
    reply: str | None = None


FINANCE_KEYWORDS = {
    "budget", "expense", "expenses", "income", "salary", "rent", "bill", "bills",
    "subscription", "recurring", "save", "saving", "savings", "spend", "spending",
    "afford", "debt", "loan", "credit", "cash", "bank", "card", "money", "finance",
    "invest", "investment", "tax", "buy", "purchase", "goal", "plan",
}

SMALLTALK_KEYWORDS = {
    "hi", "hello", "hey", "thanks", "thank you", "good morning", "good evening",
    "how are you", "what's up", "whats up",
}

SENSITIVE_OUT_OF_SCOPE_KEYWORDS = {
    "medical", "diagnosis", "prescription", "lawyer", "legal advice", "court",
    "self harm", "suicide", "weapon", "hack", "exploit",
}


def route_message(text: str) -> RouteDecision:
    """Classify message into allowed vs out-of-scope policy buckets."""
    normalized = (text or "").strip().lower()

    if not normalized:
        return RouteDecision(
            mode="empathetic_redirect",
            category="empty",
            reply="I am here for your money planning. Tell me an expense, income, or a purchase goal to plan.",
        )

    if any(token in normalized for token in SENSITIVE_OUT_OF_SCOPE_KEYWORDS):
        return RouteDecision(
            mode="decline",
            category="sensitive_out_of_scope",
            reply=(
                "I am best at personal finance coaching and cannot help with that topic. "
                "If you want, I can help you plan a budget or a savings goal."
            ),
        )

    if any(token in normalized for token in FINANCE_KEYWORDS):
        return RouteDecision(mode="finance_agent", category="finance")

    if any(token in normalized for token in SMALLTALK_KEYWORDS):
        return RouteDecision(
            mode="empathetic_redirect",
            category="smalltalk",
            reply="Happy to chat. I am your finance coach here, so ask me about spending, saving, or a purchase plan.",
        )

    return RouteDecision(
        mode="decline",
        category="out_of_scope",
        reply=(
            "I keep this chat focused on personal finance so advice stays useful and grounded. "
            "Ask me about budgeting, expenses, recurring bills, or whether a purchase is a good idea now."
        ),
    )
