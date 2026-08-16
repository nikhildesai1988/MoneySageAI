"""
bot.py - MoneySage main bot process

Telegram bot with long-polling + in-process scheduler for daily/weekly/monthly jobs.
"""

from dotenv import load_dotenv
load_dotenv()  # Load .env before importing config

import logging
import re

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import config
import db
from handlers import commands, messages, jobs


class _SensitiveLogFilter(logging.Filter):
    """Redact tokens/keys from any log record text before emission."""

    _PATTERNS = (
        # Telegram bot token in URL path: /bot<token>/...
        re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+", re.IGNORECASE),
        # Bot token in plain text: bot<token>
        re.compile(r"\bbot\d+:[A-Za-z0-9_-]+\b", re.IGNORECASE),
        # Common API key format in env/log dumps.
        re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
        # Authorization bearer token values.
        re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._-]+"),
    )

    def _redact_text(self, text: str) -> str:
        redacted = text
        redacted = redacted.replace(config.BOT_TOKEN, "<TELEGRAM_BOT_TOKEN_REDACTED>")
        for pattern in self._PATTERNS:
            if "authorization" in pattern.pattern.lower():
                redacted = pattern.sub(r"\1<REDACTED>", redacted)
            elif pattern.pattern.startswith("(/bot)"):
                redacted = pattern.sub(r"\1<TELEGRAM_BOT_TOKEN_REDACTED>", redacted)
            else:
                redacted = pattern.sub("<REDACTED>", redacted)
        return redacted

    def filter(self, record: logging.LogRecord) -> bool:
        original_message = record.getMessage()
        cleaned_message = self._redact_text(original_message)

        if cleaned_message != original_message:
            record.msg = cleaned_message
            record.args = ()

        return True


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
_sensitive_filter = _SensitiveLogFilter()
for _handler in logging.getLogger().handlers:
    _handler.addFilter(_sensitive_filter)

log = logging.getLogger("finbot")

# Suppress noisy Telegram HTTP request info logs; keep warnings/errors visible.
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)


def main():
    """Initialize and run the Telegram bot."""
    db.init_db()
    app = Application.builder().token(config.BOT_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", commands.start))
    app.add_handler(CommandHandler("balance", commands.balance))
    app.add_handler(CommandHandler("recurring", commands.recurring))
    app.add_handler(CommandHandler("summary_week", commands.summary_week))
    app.add_handler(CommandHandler("summaryweek", commands.summary_week))
    app.add_handler(CommandHandler("summary_month", commands.summary_month))
    app.add_handler(CommandHandler("summarymonth", commands.summary_month))
    app.add_handler(CommandHandler("usage", commands.usage))
    app.add_handler(CommandHandler("help", commands.help_command))

    # Message handler (must be after commands)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, messages.handle_message))

    # Scheduler for jobs
    scheduler = AsyncIOScheduler(timezone=config.TIMEZONE)
    scheduler.add_job(jobs.daily_job, CronTrigger(hour=8, minute=0), kwargs={"context": app})
    scheduler.add_job(jobs.weekly_job, CronTrigger(day_of_week="sun", hour=18, minute=0), kwargs={"context": app})
    scheduler.add_job(jobs.monthly_job, CronTrigger(day="last", hour=20, minute=0), kwargs={"context": app})
    scheduler.start()

    log.info(
        "MoneySage starting (timezone=%s, owner_chat_id=%s, monthly_budget=%s%.2f, hard_cap=%s)",
        config.TIMEZONE, config.OWNER_CHAT_ID, config.CURRENCY, config.MONTHLY_BUDGET_USD, config.HARD_CAP,
    )
    app.run_polling()


if __name__ == "__main__":
    main()
