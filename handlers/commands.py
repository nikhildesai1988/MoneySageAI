"""
handlers/commands.py - Telegram command handlers
"""

import asyncio
from datetime import date, timedelta
import logging

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ai import models, budget
from messages import HELP_MESSAGE, BUDGET_HARD_STOP_MESSAGE, BUDGET_WARN_MESSAGE
from config import OWNER_CHAT_ID, CURRENCY, MONTHLY_BUDGET_USD, HARD_CAP
import db


log = logging.getLogger("finbot")


def _display_name(update: Update) -> str | None:
    user = update.effective_user
    if not user:
        return None
    return user.first_name or user.username or None


def _personalize_text(update: Update, text: str) -> str:
    name = _display_name(update)
    if not name:
        return text
    return f"{name}, {text}"


def _personalize_markdown_intro(update: Update, text: str) -> str:
    name = _display_name(update)
    if not name:
        return text
    return f"Hi {name}.\n\n{text}"


async def _typing_heartbeat(bot, chat_id: int, stop_event: asyncio.Event):
    """Send periodic typing action while long operations are running."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as exc:
            log.debug("typing heartbeat error in command handler: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            continue


def format_month_day(day: int) -> str:
    """Format day number as an ordinal monthly phrase, e.g. '5th of every month'."""
    day = int(day)
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} of every month"


def owner_only(func):
    """Decorator to restrict handlers to owner chat only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat.id != OWNER_CHAT_ID:
            # Silent ignore, log it
            import logging
            log = logging.getLogger("finbot")
            log.warning(
                "Blocked message from non-owner chat_id=%s username=%s",
                chat.id, chat.username,
            )
            return
        return await func(update, context)
    return wrapper


@owner_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command."""
    await update.message.reply_text(
        _personalize_text(
            update,
            "MoneySage online. I run as an autonomous finance agent with tool-calling.\n\n"
            "What I do automatically:\n"
            "• Plan steps and call finance tools (read/write)\n"
            "• Ask clarifying questions when details are missing\n"
            "• Verify write actions with follow-up reads\n"
            "• Warn about similar adds but still let you add intentionally\n"
            "• Generate AI-driven daily/weekly/monthly updates\n\n"
            "Try messages like:\n"
            "• \"Spent 45 on groceries with Discover\"\n"
            "• \"Got salary 3000\"\n"
            "• \"Add Netflix 15 monthly on Visa\"\n"
            "• \"Cancel my Netflix\"\n"
            "• \"Can I afford a 200 jacket this week?\"\n\n"
            "Commands: /help /balance /recurring /summary_week /summary_month /usage",
        )
    )


@owner_only
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /balance command."""
    ym = date.today().strftime("%Y-%m")
    s = db.month_summary(ym)
    await update.message.reply_text(
        _personalize_text(
            update,
            f"This month so far:\nIncome: {CURRENCY}{s['income']:.2f}\n"
            f"Expenses: {CURRENCY}{s['expense']:.2f}\nNet: {CURRENCY}{s['net']:.2f}",
        )
    )


@owner_only
async def recurring(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /recurring command."""
    charges = db.get_active_recurring_charges()
    if not charges:
        await update.message.reply_text(_personalize_text(update, "No active recurring charges."))
        return
    lines = [
        f"• {c['name']}: {CURRENCY}{c['amount']:.2f} on the {format_month_day(c['day_of_month'])}"
        + (f" (ends {c['end_date']})" if c["end_date"] else "")
        for c in charges
    ]
    await update.message.reply_text(_personalize_text(update, "Active recurring charges:\n" + "\n".join(lines)))


@owner_only
async def summary_week(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summary_week command."""
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_heartbeat(context.bot, update.effective_chat.id, stop_typing)
    )
    try:
        summary = await asyncio.wait_for(asyncio.to_thread(build_weekly_summary), timeout=45)
    except budget.BudgetExceededError:
        summary = BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)
    except TimeoutError:
        summary = (
            "Weekly summary is taking longer than expected. "
            "Please try again in a moment."
        )
    except Exception as exc:
        log.exception("summary_week failed: %s", exc)
        summary = "I hit an error while building your weekly summary. Please try again."
    finally:
        stop_typing.set()
        await typing_task

    await update.message.reply_text(_personalize_text(update, summary))


@owner_only
async def summary_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /summary_month command."""
    ym = date.today().strftime("%Y-%m")
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_heartbeat(context.bot, update.effective_chat.id, stop_typing)
    )
    try:
        summary = await asyncio.wait_for(asyncio.to_thread(build_monthly_summary, ym), timeout=45)
    except budget.BudgetExceededError:
        summary = BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)
    except TimeoutError:
        summary = (
            "Monthly summary is taking longer than expected. "
            "Please try again in a moment."
        )
    except Exception as exc:
        log.exception("summary_month failed: %s", exc)
        summary = "I hit an error while building your monthly summary. Please try again."
    finally:
        stop_typing.set()
        await typing_task

    await update.message.reply_text(_personalize_text(update, summary))


@owner_only
async def usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /usage command."""
    status = budget.get_budget_status()
    bar_len = 20
    filled = min(bar_len, int(status["ratio"] * bar_len))
    bar = "█" * filled + "░" * (bar_len - filled)
    await update.message.reply_text(
        _personalize_text(
            update,
            f"Claude API usage this month ({status['period']}):\n"
            f"{bar} {status['ratio']*100:.0f}%\n"
            f"{CURRENCY}{status['spend_usd']:.4f} / {CURRENCY}{status['budget_usd']:.2f}\n"
            f"Hard cap: {'ON' if HARD_CAP else 'OFF'}",
        )
    )


@owner_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    await update.message.reply_text(_personalize_markdown_intro(update, HELP_MESSAGE), parse_mode="Markdown")


def build_weekly_summary() -> str:
    """Build weekly spending summary."""
    today = date.today()
    start = today - timedelta(days=7)
    txns = db.get_transactions(start_date=start.isoformat(), end_date=today.isoformat())
    income = sum(t["amount"] for t in txns if t["type"] == "income")
    expense = sum(t["amount"] for t in txns if t["type"] == "expense")
    by_cat = {}
    for t in txns:
        if t["type"] == "expense":
            by_cat[t["category"] or "uncategorized"] = by_cat.get(t["category"] or "uncategorized", 0) + t["amount"]
    data = {"income": income, "expense": expense, "net": income - expense, "by_category": by_cat}
    return models.generate_summary(f"Week of {start.isoformat()} to {today.isoformat()}", data)


def build_monthly_summary(year_month: str) -> str:
    """Build monthly spending summary."""
    data = db.month_summary(year_month)
    return models.generate_summary(f"Month {year_month}", data)


async def maybe_warn_about_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Warn once a day if budget usage crosses 80%."""
    status = budget.get_budget_status()
    if 0.8 <= status["ratio"] < 1.0:
        today_key = f"budget_warn_{date.today().isoformat()}"
        if not context.bot_data.get(today_key):
            context.bot_data[today_key] = True
            await update.message.reply_text(
                BUDGET_WARN_MESSAGE.format(
                    pct=status["ratio"] * 100, currency=CURRENCY,
                    budget=status["budget_usd"], spend=status["spend_usd"],
                )
            )
