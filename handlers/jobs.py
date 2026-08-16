"""
handlers/jobs.py - Scheduled APScheduler jobs
"""

from datetime import date

from telegram.ext import ContextTypes

from ai import models, budget
from messages import BUDGET_HARD_STOP_MESSAGE, BUDGET_WARN_MESSAGE
from config import OWNER_CHAT_ID, CURRENCY, MONTHLY_BUDGET_USD
import db


async def daily_job(context: ContextTypes.DEFAULT_TYPE):
    """Send daily reminders and gut-check message."""
    try:
        message = models.run_scheduled_agent(
            "Create today's daily update. Use tools to: "
            "1) fetch upcoming unreminded charges for the next 3 days, "
            "2) check current month trend and budget status, "
            "3) produce a concise Telegram message with any reminders and a spending gut-check. "
            "When recurring schedules appear, prefer humanized wording like '5th of every month'."
        )
    except budget.BudgetExceededError:
        message = BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)
    await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=message)


async def weekly_job(context: ContextTypes.DEFAULT_TYPE):
    """Send weekly spending summary."""
    try:
        summary = models.run_scheduled_agent(
            "Create a weekly summary message. Use tools to read the last 7 days of transactions, "
            "active recurring charges, and budget status. Return one concise actionable summary. "
            "When referencing recurring schedules, use phrases like '16th of every month'."
        )
    except budget.BudgetExceededError:
        summary = BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)
    await context.bot.send_message(chat_id=OWNER_CHAT_ID, text="📅 Weekly summary\n\n" + summary)


async def monthly_job(context: ContextTypes.DEFAULT_TYPE):
    """Send monthly spending summary."""
    ym = date.today().strftime("%Y-%m")
    try:
        summary = models.run_scheduled_agent(
            f"Create a monthly summary for {ym}. Use tools to fetch month summary, "
            "largest expense categories, payment methods, recurring charges, and budget status. "
            "Return concise insights and one or two specific suggestions. "
            "If you mention recurring charge timing, use readable phrases like '6th of every month'."
        )
    except budget.BudgetExceededError:
        summary = BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)
    await context.bot.send_message(chat_id=OWNER_CHAT_ID, text="📆 Monthly summary\n\n" + summary)
