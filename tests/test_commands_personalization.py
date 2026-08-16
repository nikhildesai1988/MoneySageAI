import asyncio

from handlers import commands


def test_start_command_is_personalized(fake_update, fake_context):
    asyncio.run(commands.start(fake_update, fake_context))
    assert fake_update.message.replies
    assert fake_update.message.replies[-1]["text"].startswith("Nikhil, MoneySage online.")


def test_help_command_is_personalized(fake_update, fake_context):
    asyncio.run(commands.help_command(fake_update, fake_context))
    assert fake_update.message.replies
    assert "Hi Nikhil." in fake_update.message.replies[-1]["text"]


def test_balance_command_is_personalized(fake_update, fake_context, monkeypatch):
    monkeypatch.setattr(commands.db, "month_summary", lambda ym: {"income": 1000.0, "expense": 200.0, "net": 800.0})
    asyncio.run(commands.balance(fake_update, fake_context))
    assert fake_update.message.replies[-1]["text"].startswith("Nikhil, This month so far:")
