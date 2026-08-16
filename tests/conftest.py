import os
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest


# Set required env vars before app modules import config.py
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OWNER_CHAT_ID", "12345")
os.environ.setdefault("AI_PROVIDER", "ollama")
os.environ.setdefault("TIMEZONE", "America/New_York")
os.environ.setdefault("MONTHLY_BUDGET_USD", "5.00")
os.environ.setdefault("ENFORCE_HARD_CAP", "false")


@dataclass
class FakeChat:
    id: int
    username: str = "tester"


@dataclass
class FakeMessage:
    text: str = ""
    replies: list[dict] = field(default_factory=list)

    async def reply_text(self, text: str, parse_mode: str | None = None):
        self.replies.append({"text": text, "parse_mode": parse_mode})


class FakeBot:
    def __init__(self):
        self.sent_messages = []
        self.actions = []

    async def send_message(self, chat_id: int, text: str):
        self.sent_messages.append({"chat_id": chat_id, "text": text})

    async def send_chat_action(self, chat_id: int, action):
        self.actions.append({"chat_id": chat_id, "action": str(action)})


@dataclass
class FakeUser:
    first_name: str = "Nikhil"
    username: str = "tester"
    last_name: str | None = None


@dataclass
class FakeUpdate:
    chat_id: int = 12345
    text: str = ""
    username: str = "tester"
    first_name: str = "Nikhil"

    def __post_init__(self):
        self.effective_chat = FakeChat(id=self.chat_id, username=self.username)
        self.effective_user = FakeUser(first_name=self.first_name, username=self.username)
        self.message = FakeMessage(text=self.text)


@dataclass
class FakeContext:
    bot: FakeBot = field(default_factory=FakeBot)
    user_data: dict = field(default_factory=dict)
    bot_data: dict = field(default_factory=dict)


@pytest.fixture
def fake_update():
    return FakeUpdate()


@pytest.fixture
def fake_context():
    return FakeContext()


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    import db

    db_file = tmp_path / "finbot.db"
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db.init_db()
    return db_file


@pytest.fixture
def fake_anthropic_response():
    class Usage:
        input_tokens = 0
        output_tokens = 0

    class Resp:
        usage = Usage()
        content = [SimpleNamespace(text='{"action":"final","message":"ok"}')]

    return Resp()
