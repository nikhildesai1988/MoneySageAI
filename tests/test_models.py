from types import SimpleNamespace

import ai.models as models


class FakeResp:
    def __init__(self, text: str):
        self.content = [SimpleNamespace(text=text)]


def test_run_agent_plan_tool_final(monkeypatch):
    outputs = iter(
        [
            FakeResp('{"action":"plan","steps":["read txns"]}'),
            FakeResp('{"action":"tool","tool_name":"get_month_summary","args":{"year_month":"2026-08"}}'),
            FakeResp('{"action":"final","message":"You spent $50 this month."}'),
        ]
    )

    monkeypatch.setattr(models.budget, "check_budget_before_call", lambda: None)
    monkeypatch.setattr(models.provider, "call_model", lambda *a, **k: next(outputs))
    monkeypatch.setattr(models.provider, "extract_text", lambda resp: resp.content[0].text)
    monkeypatch.setattr(models.tools, "execute_tool", lambda name, args: {"income": 0, "expense": 50, "net": -50})

    result = models.run_agent("How am I doing?")

    assert result["ok"] is True
    assert "spent" in result["message"].lower()
    assert any("tool" in step for step in result["steps"])


def test_run_agent_clarify(monkeypatch):
    monkeypatch.setattr(models.budget, "check_budget_before_call", lambda: None)
    monkeypatch.setattr(
        models.provider,
        "call_model",
        lambda *a, **k: FakeResp('{"action":"clarify","question":"Which card?","required_fields":["payment_method"]}'),
    )
    monkeypatch.setattr(models.provider, "extract_text", lambda resp: resp.content[0].text)

    result = models.run_agent("I spent 40")

    assert result["ok"] is True
    assert result["needs_clarification"] is True
    assert result["required_fields"] == ["payment_method"]
