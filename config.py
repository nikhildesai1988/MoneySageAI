"""
config.py - Configuration and environment variables
"""

import os

# Telegram
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])

# AI Provider
AI_PROVIDER = os.environ.get("AI_PROVIDER", "anthropic").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

# Runtime
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")
CURRENCY = os.environ.get("CURRENCY_SYMBOL", "$")
MONTHLY_BUDGET_USD = float(os.environ.get("MONTHLY_BUDGET_USD", "5.00"))
HARD_CAP = os.environ.get("ENFORCE_HARD_CAP", "true").lower() == "true"

# Anthropic pricing (Claude Sonnet 5 as of Aug 2026)
PRICE_PER_INPUT_TOKEN = 2.0 / 1_000_000
PRICE_PER_OUTPUT_TOKEN = 10.0 / 1_000_000
MODEL = "claude-sonnet-5"
