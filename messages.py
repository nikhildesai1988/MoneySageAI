"""
messages.py - All user-facing messages and help text
"""

from config import CURRENCY

BUDGET_WARN_MESSAGE = (
    "⚠️ Heads up: this bot's Claude API usage has hit {pct:.0f}% of its "
    "{currency}{budget:.2f} monthly budget ({currency}{spend:.2f} spent so far)."
)

BUDGET_HARD_STOP_MESSAGE = (
    "This bot has hit its monthly Claude API budget ({currency}{budget:.2f}), so I can't "
    "process free-text messages until next month or until you raise MONTHLY_BUDGET_USD. "
    "Basic commands like /balance and /recurring still work since they don't call the API."
)

HELP_MESSAGE = """
📊 *MoneySage - Autonomous Finance Agent*

*All Commands:*

/start - Show a quick intro and examples
/help - Show this detailed help
/balance - Current month income, expenses, and net
/recurring - List all active recurring charges
/summary_week - Weekly spending summary with advice
/summary_month - Monthly spending summary with advice
/usage - Show Claude API spend against monthly budget

*Natural Language Examples (Agentic):*

💰 *Log Expense:*
"Paid 45 for groceries with Discover"
"Spent 20 on coffee"

💵 *Log Income:*
"Got salary 3000"
"Freelance payment 500"

🔄 *Add Recurring:*
"Netflix 15/month starting today"
"Gym membership 50/month"

❌ *Cancel Recurring:*
"Cancel my Netflix"
"Remove gym"

❓ *Ask Questions:*
"How much did I spend on food this month?"
"What's my net balance this week?"
"Can I afford a 200 jacket right now?"

🧠 *Agent Behaviors:*
✓ Plans actions and calls tools to read/write your finance data
✓ Asks clarifying questions when inputs are missing or ambiguous
✓ Verifies write operations with follow-up read checks
✓ Prevents duplicate writes with idempotency protection
✓ Remembers preferences through persistent preference tools

⚙️ *System Features:*
✓ AI-driven daily, weekly, and monthly updates
✓ AI-powered financial advice grounded in your own data
✓ Budget cap to prevent overspending on AI costs
✓ Local data storage (SQLite)
✓ Owner-only access via OWNER_CHAT_ID
✓ Finance-focused scope with empathetic redirects for unrelated topics

*Budget Protection:*
Every AI call is metered and tracked. Your monthly budget prevents surprise bills.
Use /usage to check current spending.
"""
