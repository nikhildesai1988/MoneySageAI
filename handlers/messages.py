"""
handlers/messages.py - Telegram message handlers
"""

import asyncio
import logging
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ai import models, budget
from ai.guardrails import route_message
from messages import HELP_MESSAGE, BUDGET_HARD_STOP_MESSAGE, BUDGET_WARN_MESSAGE
from config import OWNER_CHAT_ID, MONTHLY_BUDGET_USD, CURRENCY
import db

log = logging.getLogger("finbot")


async def _typing_heartbeat(bot, chat_id: int, stop_event: asyncio.Event):
    """Send periodic typing action while long operations are running."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception as exc:
            log.debug("typing heartbeat error: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            continue


def owner_only(func):
    """Decorator to restrict handlers to owner chat only."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat.id != OWNER_CHAT_ID:
            import logging
            log = logging.getLogger("finbot")
            log.warning(
                "Blocked message from non-owner chat_id=%s username=%s",
                chat.id, chat.username,
            )
            return
        return await func(update, context)
    return wrapper


async def maybe_warn_about_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Warn once a day if budget usage crosses 80%."""
    from datetime import date
    
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


@owner_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language financial messages."""
    text = (update.message.text or "").strip()
    normalized = text.lower()

    greeting_phrases = {
        "hi", "hello", "hey", "hey there", "good morning", "good afternoon",
        "good evening", "yo", "hi there", "hello there"
    }
    if normalized in greeting_phrases or normalized.startswith(("hi ", "hello ", "hey ", "yo ")):
        await update.message.reply_text(HELP_MESSAGE, parse_mode="Markdown")
        return

    # If we asked a follow-up question (e.g., payment method), handle that reply first.
    # This avoids guardrails incorrectly declining short context-dependent answers.
    if context.user_data.get("awaiting_payment_method"):
        pending = context.user_data.pop("awaiting_payment_method")
        payment_method = text.strip()
        
        # Log the transaction with the provided payment method
        intent = pending["intent"]
        if intent == "log_expense":
            db.add_transaction(
                "expense", pending["amount"], pending.get("category"),
                pending.get("description"), pending.get("txn_date"),
                payment_method=payment_method
            )
            await update.message.reply_text(
                f"Logged expense: {CURRENCY}{pending['amount']:.2f} "
                f"({pending.get('category') or 'uncategorized'}) via {payment_method}"
            )
        elif intent == "log_income":
            db.add_transaction(
                "income", pending["amount"], pending.get("category") or "income",
                pending.get("description"), pending.get("txn_date"),
                payment_method=payment_method
            )
            await update.message.reply_text(
                f"Logged income: {CURRENCY}{pending['amount']:.2f} from {payment_method}"
            )
        elif intent == "add_recurring":
            db.add_recurring_charge(
                pending["recurring_name"], pending["amount"],
                pending.get("recurring_day_of_month") or 1,
                end_date=pending.get("recurring_end_date"),
                payment_method=payment_method
            )
            await update.message.reply_text(
                f"Added recurring charge: {pending['recurring_name']} "
                f"({CURRENCY}{pending['amount']:.2f}/month) via {payment_method}"
            )
        await maybe_warn_about_budget(update, context)
        return

    # Scope and safety guardrails: keep agent focused on personal finance coaching.
    decision = route_message(text)
    if decision.mode in ("empathetic_redirect", "decline"):
        await update.message.reply_text(decision.reply or "I can help with personal finance topics.")
        return

    # Autonomous agent path: model decides tool calls and final response.
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_heartbeat(context.bot, update.effective_chat.id, stop_typing)
    )
    try:
        # run_agent is synchronous and can block on model/tool calls, so run it in a worker thread
        # to keep typing indicators responsive.
        agent_result = await asyncio.wait_for(
            asyncio.to_thread(models.run_agent, text),
            timeout=45,
        )
    except budget.BudgetExceededError:
        stop_typing.set()
        await typing_task
        await update.message.reply_text(
            BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)
        )
        return
    except TimeoutError:
        stop_typing.set()
        await typing_task
        await update.message.reply_text(
            "This is taking longer than expected. I may be waiting on the model. "
            "Please try again, or ask with a more specific question (e.g. '/usage' for budget status)."
        )
        return
    finally:
        stop_typing.set()
        await typing_task

    # Deterministic fallback for income-status style questions.
    if agent_result.get("ok"):
        msg = (agent_result.get("message") or "").strip()
        lower_text = normalized
        asks_income_status = (
            "income" in lower_text
            and ("status" in lower_text or "how much" in lower_text or "current" in lower_text)
        )
        if asks_income_status and (not msg or "let me check" in msg.lower()):
            from datetime import date
            ym = date.today().strftime("%Y-%m")
            summary = db.month_summary(ym)
            await update.message.reply_text(
                f"Your income status for {ym}:\n"
                f"Income: {CURRENCY}{summary['income']:.2f}\n"
                f"Expenses: {CURRENCY}{summary['expense']:.2f}\n"
                f"Net: {CURRENCY}{summary['net']:.2f}"
            )
            await maybe_warn_about_budget(update, context)
            return

    if agent_result.get("ok"):
        await update.message.reply_text(agent_result.get("message") or "Done.")
    else:
        await update.message.reply_text(
            agent_result.get("message")
            or "I couldn't complete that request right now. Please try again."
        )

    await maybe_warn_about_budget(update, context)
