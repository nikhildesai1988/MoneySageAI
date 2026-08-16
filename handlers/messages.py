"""
handlers/messages.py - Telegram message handlers
"""

import asyncio
import logging
from typing import Any
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from ai import models, budget
from ai.guardrails import route_message
from messages import HELP_MESSAGE, BUDGET_HARD_STOP_MESSAGE, BUDGET_WARN_MESSAGE
from config import OWNER_CHAT_ID, MONTHLY_BUDGET_USD, CURRENCY
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


def _is_recurring_list_request(text: str) -> bool:
    normalized = (text or "").strip().lower()
    wants_list = any(token in normalized for token in ("list", "show", "what", "which", "all"))
    recurring_topic = any(token in normalized for token in ("recurring", "subscription", "subscriptions", "bills"))
    balance_like = any(token in normalized for token in ("balance", "after", "afford", "remaining", "left", "net", "total"))
    return wants_list and recurring_topic and not balance_like


def _is_balance_question(text: str) -> bool:
    normalized = (text or "").strip().lower()
    if not normalized:
        return False
    return any(token in normalized for token in ("balance", "how much", "current", "remaining", "left", "net", "after", "afford"))


def _format_month_day(day: int) -> str:
    day = int(day)
    if 10 <= day % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix} of every month"


def _looks_like_new_request(text: str) -> bool:
    """Heuristic to detect a fresh intent instead of a clarification answer."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return False

    starts_with = (
        "add ", "log ", "spent ", "paid ", "got ", "received ", "show ",
        "list ", "cancel ", "remove ", "help", "summary", "balance",
        "can i ", "how much", "what",
    )
    return normalized.startswith(starts_with)


def _preview(text: str, max_len: int = 220) -> str:
    cleaned = (text or "").replace("\n", " ").strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def _mentions_payment_method(text: str) -> bool:
    """Heuristic: return True only when user text explicitly mentions payment source."""
    normalized = f" {(text or '').strip().lower()} "
    tokens = (
        " cash ", " card ", " credit card ", " debit card ",
        " visa ", " mastercard ", " discover ", " amex ", " american express ",
        " bank ", " bank account ", " checking ", " savings ",
        " paypal ", " venmo ", " zelle ", " apple pay ", " google pay ",
        " upi ", " check ", " wire ", " ach ",
    )
    prepositions = (" with ", " via ", " from ", " using ", " through ", " on ", " to ")
    if any(token in normalized for token in tokens):
        return True
    return any(prep in normalized for prep in prepositions) and any(token.strip() in normalized for token in tokens)


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_category(extracted: dict, default: str = "uncategorized") -> str:
    category = (extracted.get("category") or "").strip() if isinstance(extracted.get("category"), str) else extracted.get("category")
    if isinstance(category, str) and category:
        return category

    description = extracted.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()

    return default


def _maybe_build_payment_follow_up(extracted: dict | None, user_text: str) -> tuple[dict, str] | None:
    """Build pending state when extractor found a finance write intent without payment method."""
    if not isinstance(extracted, dict):
        return None

    intent = extracted.get("intent")
    payment_method = extracted.get("payment_method")
    has_explicit_payment = _mentions_payment_method(user_text)
    if intent not in {"log_expense", "log_income", "add_recurring"}:
        return None

    # Even if model guessed a payment method, require explicit mention in user text.
    if payment_method and has_explicit_payment:
        return None

    amount = _to_float(extracted.get("amount"))
    if amount is None or amount <= 0:
        return None

    if intent == "add_recurring":
        name = extracted.get("recurring_name")
        if not name:
            return None
        pending = {
            "intent": "add_recurring",
            "recurring_name": name,
            "amount": amount,
            "recurring_day_of_month": extracted.get("recurring_day_of_month") or 1,
            "recurring_end_date": extracted.get("recurring_end_date"),
        }
    else:
        pending = {
            "intent": "log_expense" if intent == "log_expense" else "log_income",
            "amount": amount,
            "category": _resolve_category(extracted, default="income" if intent == "log_income" else "uncategorized"),
            "description": extracted.get("description"),
            "txn_date": extracted.get("txn_date"),
        }

    prompt = "Which payment method should I use (e.g., Cash, Discover, Bank Account)?"
    return pending, prompt


def _normalize_day_of_month(value: Any, default: int = 1) -> int:
    try:
        day = int(value)
    except (TypeError, ValueError):
        return default
    return min(28, max(1, day))


@owner_only
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle natural language financial messages."""
    text = (update.message.text or "").strip()
    normalized = text.lower()
    pending_clarification = context.user_data.get("awaiting_clarification")
    log.info("[chat] input: %s", _preview(text))

    async def _reply(text_out: str, parse_mode: str | None = None):
        log.info("[chat] output: %s", _preview(text_out))
        await update.message.reply_text(text_out, parse_mode=parse_mode)

    greeting_phrases = {
        "hi", "hello", "hey", "hey there", "good morning", "good afternoon",
        "good evening", "yo", "hi there", "hello there"
    }
    if normalized in greeting_phrases or normalized.startswith(("hi ", "hello ", "hey ", "yo ")):
        await _reply(_personalize_markdown_intro(update, HELP_MESSAGE), parse_mode="Markdown")
        return

    if pending_clarification and _looks_like_new_request(text):
        # User started a new request; drop stale clarification context.
        context.user_data.pop("awaiting_clarification", None)
        pending_clarification = None

    # Balance/affordability questions should go to the AI path, not the recurring-charge list shortcut.
    if _is_balance_question(text):
        pending_clarification = context.user_data.get("awaiting_clarification")
        # Fall through to the AI/router path below.

    # Deterministic recurring list request path (avoid model choosing reminder-only tools).
    if _is_recurring_list_request(text):
        charges = db.get_active_recurring_charges()
        if not charges:
            await _reply(_personalize_text(update, "No active recurring charges."))
            return
        lines = [
            f"- {c['name']}: {CURRENCY}{c['amount']:.2f} on the {_format_month_day(c['day_of_month'])}"
            + (f" (ends {c['end_date']})" if c.get("end_date") else "")
            for c in charges
        ]
        await _reply(_personalize_text(update, "Active recurring charges:\n" + "\n".join(lines)))
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
            await _reply(
                _personalize_text(
                    update,
                    f"Logged expense: {CURRENCY}{pending['amount']:.2f} "
                    f"({pending.get('category') or 'uncategorized'}) via {payment_method}",
                )
            )
        elif intent == "log_income":
            db.add_transaction(
                "income", pending["amount"], pending.get("category") or "income",
                pending.get("description"), pending.get("txn_date"),
                payment_method=payment_method
            )
            await _reply(_personalize_text(update, f"Logged income: {CURRENCY}{pending['amount']:.2f} from {payment_method}"))
        elif intent == "add_recurring":
            db.add_recurring_charge(
                pending["recurring_name"], pending["amount"],
                pending.get("recurring_day_of_month") or 1,
                end_date=pending.get("recurring_end_date"),
                payment_method=payment_method
            )
            await _reply(
                _personalize_text(
                    update,
                    f"Added recurring charge: {pending['recurring_name']} "
                    f"({CURRENCY}{pending['amount']:.2f}/month) via {payment_method}",
                )
            )
        await maybe_warn_about_budget(update, context)
        return

    if not pending_clarification:
        extracted = None
        try:
            extracted = models.classify_and_extract(text)
            follow_up = _maybe_build_payment_follow_up(extracted, text)
        except budget.BudgetExceededError:
            await _reply(_personalize_text(update, BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)))
            return
        except Exception as exc:
            log.debug("payment-method precheck skipped: %s", exc)
            follow_up = None

        if follow_up is not None:
            pending, prompt = follow_up
            context.user_data["awaiting_payment_method"] = pending
            await _reply(_personalize_text(update, prompt))
            return

        # Deterministic structured-intent path for core write flows.
        # This avoids autonomous-loop retries issuing duplicate writes.
        if isinstance(extracted, dict):
            intent = extracted.get("intent")
            amount = _to_float(extracted.get("amount"))
            if intent == "log_expense" and amount and amount > 0:
                category = _resolve_category(extracted, default="uncategorized")
                description = extracted.get("description")
                txn_date = extracted.get("txn_date")
                payment_method = extracted.get("payment_method")
                db.add_transaction("expense", amount, category, description, txn_date, payment_method=payment_method)
                via = f" via {payment_method}" if payment_method else ""
                await _reply(_personalize_text(update, f"Logged expense: {CURRENCY}{amount:.2f} ({category}){via}"))
                await maybe_warn_about_budget(update, context)
                return

            if intent == "log_income" and amount and amount > 0:
                category = _resolve_category(extracted, default="income")
                description = extracted.get("description")
                txn_date = extracted.get("txn_date")
                payment_method = extracted.get("payment_method")
                db.add_transaction("income", amount, category, description, txn_date, payment_method=payment_method)
                via = f" via {payment_method}" if payment_method else ""
                await _reply(_personalize_text(update, f"Logged income: {CURRENCY}{amount:.2f}{via}"))
                await maybe_warn_about_budget(update, context)
                return

            if intent == "add_recurring" and amount and amount > 0:
                name = extracted.get("recurring_name")
                if name:
                    day = _normalize_day_of_month(extracted.get("recurring_day_of_month"), default=1)
                    end_date = extracted.get("recurring_end_date")
                    payment_method = extracted.get("payment_method")
                    db.add_recurring_charge(name, amount, day, end_date=end_date, payment_method=payment_method)
                    via = f" via {payment_method}" if payment_method else ""
                    await _reply(_personalize_text(update, f"Added recurring charge: {name} ({CURRENCY}{amount:.2f}/month){via}"))
                    await maybe_warn_about_budget(update, context)
                    return

    # Scope and safety guardrails: keep agent focused on personal finance coaching.
    # If the user is answering a prior clarification question, bypass guardrails and
    # continue the same finance workflow with carried context.
    if pending_clarification:
        original_request = pending_clarification.get("original_request") or ""
        required_fields = pending_clarification.get("required_fields") or []
        question = pending_clarification.get("question") or ""
        agent_input = (
            f"Original request: {original_request}\n"
            f"Clarification asked: {question}\n"
            f"Required fields: {required_fields}\n"
            f"User clarification: {text}"
        )
    else:
        decision = route_message(text)
        if decision.mode in ("empathetic_redirect", "decline"):
            await _reply(_personalize_text(update, decision.reply or "I can help with personal finance topics."))
            return
        agent_input = text

    # Autonomous agent path: model decides tool calls and final response.
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(
        _typing_heartbeat(context.bot, update.effective_chat.id, stop_typing)
    )
    try:
        # run_agent is synchronous and can block on model/tool calls, so run it in a worker thread
        # to keep typing indicators responsive.
        agent_result = await asyncio.wait_for(
            asyncio.to_thread(models.run_agent, agent_input),
            timeout=45,
        )
    except budget.BudgetExceededError:
        stop_typing.set()
        await typing_task
        await _reply(_personalize_text(update, BUDGET_HARD_STOP_MESSAGE.format(currency=CURRENCY, budget=MONTHLY_BUDGET_USD)))
        return
    except TimeoutError:
        context.user_data.pop("awaiting_clarification", None)
        stop_typing.set()
        await typing_task
        await _reply(
            _personalize_text(
                update,
                "This is taking longer than expected. I may be waiting on the model. "
                "Please try again, or ask with a more specific question (e.g. '/usage' for budget status).",
            )
        )
        return
    finally:
        stop_typing.set()
        await typing_task

    if agent_result.get("ok") and agent_result.get("needs_clarification"):
        context.user_data["awaiting_clarification"] = {
            "original_request": pending_clarification.get("original_request") if pending_clarification else text,
            "question": agent_result.get("message") or "",
            "required_fields": agent_result.get("required_fields") or [],
        }
        await _reply(_personalize_text(update, agent_result.get("message") or "I need one more detail."))
        return

    # Clarification loop is complete once we get a non-clarification response.
    context.user_data.pop("awaiting_clarification", None)

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
            await _reply(
                _personalize_text(
                    update,
                    f"Your income status for {ym}:\n"
                    f"Income: {CURRENCY}{summary['income']:.2f}\n"
                    f"Expenses: {CURRENCY}{summary['expense']:.2f}\n"
                    f"Net: {CURRENCY}{summary['net']:.2f}",
                )
            )
            await maybe_warn_about_budget(update, context)
            return

    if agent_result.get("ok"):
        await _reply(_personalize_text(update, agent_result.get("message") or "Done."))
    else:
        context.user_data.pop("awaiting_clarification", None)
        await _reply(
            _personalize_text(
                update,
                agent_result.get("message")
                or "I couldn't complete that request right now. Please try again.",
            )
        )

    await maybe_warn_about_budget(update, context)
