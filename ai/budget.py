"""
ai/budget.py - Budget tracking and enforcement
"""

from datetime import date

import config
import db


class BudgetExceededError(Exception):
    """Raised when monthly AI budget is exceeded and hard cap is enabled."""


def get_budget_status(period=None) -> dict:
    """Get current budget usage status."""
    period = period or date.today().strftime("%Y-%m")
    usage = db.get_monthly_spend(period)
    ratio = usage["spend_usd"] / config.MONTHLY_BUDGET_USD if config.MONTHLY_BUDGET_USD > 0 else 0
    return {
        "period": period,
        "spend_usd": usage["spend_usd"],
        "budget_usd": config.MONTHLY_BUDGET_USD,
        "ratio": ratio,
        "over_budget": ratio >= 1.0,
    }


def check_budget_before_call():
    """Raise BudgetExceededError if hard cap is enabled and budget is exceeded."""
    if not config.HARD_CAP:
        return
    status = get_budget_status()
    if status["over_budget"]:
        raise BudgetExceededError(
            f"Monthly AI budget ({config.CURRENCY}{config.MONTHLY_BUDGET_USD:.2f}) reached "
            f"({config.CURRENCY}{status['spend_usd']:.2f} spent). Set MONTHLY_BUDGET_USD higher "
            f"or wait until next month."
        )


def log_usage(input_tokens: int, output_tokens: int) -> float:
    """Log API usage and return cost."""
    cost = (
        input_tokens * config.PRICE_PER_INPUT_TOKEN
        + output_tokens * config.PRICE_PER_OUTPUT_TOKEN
    )
    db.record_api_usage(input_tokens, output_tokens, cost)
    return cost
