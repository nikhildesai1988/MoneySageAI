import asyncio

from handlers import commands, jobs


def test_summary_week_command_uses_generated_summary(fake_update, fake_context, monkeypatch):
    fake_update.text = "/summary_week"
    monkeypatch.setattr(commands, "build_weekly_summary", lambda: "Weekly: net +$200")

    asyncio.run(commands.summary_week(fake_update, fake_context))

    assert fake_update.message.replies
    assert "Weekly:" in fake_update.message.replies[-1]["text"]


def test_summary_month_command_uses_generated_summary(fake_update, fake_context, monkeypatch):
    fake_update.text = "/summary_month"
    monkeypatch.setattr(commands, "build_monthly_summary", lambda ym: f"Monthly ({ym}): net +$400")

    asyncio.run(commands.summary_month(fake_update, fake_context))

    assert fake_update.message.replies
    assert "Monthly" in fake_update.message.replies[-1]["text"]


def test_daily_job_sends_message_to_owner(fake_context, monkeypatch):
    monkeypatch.setattr(jobs.models, "run_scheduled_agent", lambda prompt: "Daily digest")

    asyncio.run(jobs.daily_job(fake_context))

    assert fake_context.bot.sent_messages
    msg = fake_context.bot.sent_messages[-1]
    assert msg["chat_id"] == jobs.OWNER_CHAT_ID
    assert msg["text"] == "Daily digest"


def test_weekly_job_prefixes_message(fake_context, monkeypatch):
    monkeypatch.setattr(jobs.models, "run_scheduled_agent", lambda prompt: "You spent less this week")

    asyncio.run(jobs.weekly_job(fake_context))

    msg = fake_context.bot.sent_messages[-1]
    assert msg["chat_id"] == jobs.OWNER_CHAT_ID
    assert msg["text"].startswith("📅 Weekly summary")


def test_monthly_job_prefixes_message(fake_context, monkeypatch):
    monkeypatch.setattr(jobs.models, "run_scheduled_agent", lambda prompt: "Month closed positive")

    asyncio.run(jobs.monthly_job(fake_context))

    msg = fake_context.bot.sent_messages[-1]
    assert msg["chat_id"] == jobs.OWNER_CHAT_ID
    assert msg["text"].startswith("📆 Monthly summary")
