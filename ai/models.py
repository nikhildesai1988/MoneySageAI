"""
ai/models.py - Core AI model calls for finance parsing and advice
"""

import json
import logging
from datetime import date

from . import provider, prompts, budget, tools
import config


log = logging.getLogger("finbot")


AGENT_SYSTEM_PROMPT = """You are MoneySage, an autonomous personal-finance agent.
You can reason over user requests and call tools to read/write the finance database.

You must respond ONLY as compact JSON using one of these actions:
0) Plan:
{"action":"plan","steps":["step1","step2"]}
1) Tool call:
{"action":"tool","tool_name":"<name>","args":{...},"reason":"short reason"}
2) Clarification question:
{"action":"clarify","question":"...","required_fields":["field1","field2"]}
3) Final response:
{"action":"final","message":"text to send user"}

Rules:
- Always call tools before making numeric claims.
- If user asks to log expense/income/recurring and payment method is missing, ask for it in final message.
- Never invent IDs or numbers.
- Keep final message concise and practical.
- Tone must be empathetic, non-judgmental, and conversational.
- Stay in personal-finance scope: budgeting, spending, saving, recurring bills, affordability planning, purchase timing.
- If user asks non-finance topics, return a concise redirect to finance coaching.
- For purchase-goal questions, provide a concrete plan using history: cashflow reality, timeline, and one or two actionable steps.
"""


def _extract_json(text: str) -> dict:
    raw = (text or "").strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _build_agent_user_prompt(user_message: str, memory: list[dict]) -> str:
    return (
        "Available tools (JSON):\n"
        f"{json.dumps(tools.TOOL_DEFS, indent=2)}\n\n"
        "Conversation/tool memory (JSON):\n"
        f"{json.dumps(memory[-12:], indent=2)}\n\n"
        f"User request:\n{user_message}\n"
    )


def run_agent(user_message: str, max_steps: int = 6) -> dict:
    """Autonomous loop: model can iteratively call tools before final response."""
    budget.check_budget_before_call()
    memory: list[dict] = []

    user_preview = (user_message or "").strip().replace("\n", " ")
    if len(user_preview) > 180:
        user_preview = user_preview[:177] + "..."
    log.info("[agent] input: %s", user_preview)

    def _is_placeholder_final(message: str) -> bool:
        text = (message or "").strip().lower()
        if not text:
            return True
        placeholders = (
            "let me check",
            "let me look",
            "one moment",
            "hold on",
            "checking that",
            "i will check",
            "let me find",
        )
        return any(p in text for p in placeholders)

    def _verification_tool_for(tool_name: str, args: dict) -> tuple[str, dict] | None:
        if tool_name == "add_transaction":
            tx_type = args.get("type_")
            txn_date = args.get("txn_date")
            if txn_date:
                return (
                    "get_transactions",
                    {
                        "start_date": txn_date,
                        "end_date": txn_date,
                        "type_": tx_type,
                        "payment_method": args.get("payment_method"),
                        "limit": 20,
                    },
                )
            return ("get_recent_transactions", {"days": 2, "type_": tx_type, "limit": 20})
        if tool_name == "add_recurring_charge":
            return ("get_active_recurring_charges", {})
        if tool_name == "remove_recurring_charge_by_name":
            return ("get_active_recurring_charges", {})
        return None

    for step_index in range(1, max_steps + 1):
        resp = provider.call_model(
            AGENT_SYSTEM_PROMPT,
            _build_agent_user_prompt(user_message, memory),
            max_tokens=700,
        )

        if config.AI_PROVIDER == "anthropic":
            budget.log_usage(resp.usage.input_tokens, resp.usage.output_tokens)

        decision = _extract_json(provider.extract_text(resp))
        action = decision.get("action")
        log.info("[agent] step %d/%d action=%s", step_index, max_steps, action)

        if action == "plan":
            steps = decision.get("steps") or []
            log.info("[agent] plan: %s", steps)
            memory.append({"plan": decision.get("steps") or []})
            continue

        if action == "clarify":
            question = decision.get("question") or "I need one clarification before I continue."
            required_fields = decision.get("required_fields")
            log.info("[agent] clarify: %s", question)
            return {
                "ok": True,
                "message": question,
                "needs_clarification": True,
                "required_fields": required_fields if isinstance(required_fields, list) else [],
                "steps": memory,
            }

        if action == "final":
            final_message = decision.get("message") or "Done."

            # Guardrail: don't finish with a placeholder unless there was actual tool work.
            saw_tool = any("tool" in item for item in memory)
            if _is_placeholder_final(final_message) and not saw_tool:
                log.info("[agent] placeholder final rejected (no tool work yet)")
                memory.append(
                    {
                        "error": "placeholder_final_without_tool",
                        "raw_message": final_message,
                    }
                )
                continue

            final_preview = final_message.replace("\n", " ")
            if len(final_preview) > 220:
                final_preview = final_preview[:217] + "..."
            log.info("[agent] output: %s", final_preview)

            return {
                "ok": True,
                "message": final_message,
                "steps": memory,
            }

        if action == "tool":
            tool_name = decision.get("tool_name")
            args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
            log.info("[agent] tool call: %s args=%s", tool_name, args)
            try:
                result = tools.execute_tool(tool_name, args)
                memory.append({"tool": tool_name, "args": args, "result": result})
                log.info("[agent] tool ok: %s", tool_name)

                # Mandatory self-check for mutating writes.
                verify = _verification_tool_for(tool_name, args)
                if verify is not None:
                    v_name, v_args = verify
                    try:
                        v_result = tools.execute_tool(v_name, v_args)
                        memory.append(
                            {
                                "verification_tool": v_name,
                                "verification_args": v_args,
                                "verification_result": v_result,
                            }
                        )
                    except Exception as v_exc:
                        log.warning("[agent] verify failed: %s err=%s", v_name, v_exc)
                        memory.append(
                            {
                                "verification_tool": v_name,
                                "verification_args": v_args,
                                "verification_error": str(v_exc),
                            }
                        )
            except Exception as exc:
                log.warning("[agent] tool failed: %s err=%s", tool_name, exc)
                memory.append({"tool": tool_name, "args": args, "error": str(exc)})
            continue

        log.warning("[agent] invalid action payload: %s", decision)
        memory.append({"error": "invalid_agent_action", "raw": decision})

    log.warning("[agent] max steps reached without final response")
    return {
        "ok": False,
        "message": "I could not complete this safely in time. Please rephrase with a bit more detail.",
        "steps": memory,
    }


def run_scheduled_agent(task_prompt: str, max_steps: int = 5) -> str:
    """Tool-driven agent path for scheduled jobs."""
    result = run_agent(task_prompt, max_steps=max_steps)
    return result.get("message") or "No update generated."


def classify_and_extract(user_message: str) -> dict:
    """Classify user message and extract structured financial data."""
    budget.check_budget_before_call()
    
    resp = provider.call_model(
        prompts.EXTRACTION_SYSTEM_PROMPT.format(today=date.today().isoformat()),
        user_message,
        max_tokens=500,
    )
    
    if config.AI_PROVIDER == "anthropic":
        budget.log_usage(resp.usage.input_tokens, resp.usage.output_tokens)
    
    text = provider.extract_text(resp)
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"intent": "unclear", "user_question": user_message}


def answer(user_question: str, context: dict) -> str:
    """Generate financial advice based on user question and context."""
    budget.check_budget_before_call()
    
    advisor_prompt = prompts.get_advisor_prompt(config.CURRENCY)
    resp = provider.call_model(
        advisor_prompt,
        f"Context (JSON):\n{json.dumps(context, indent=2)}\n\nQuestion: {user_question}",
        max_tokens=600,
    )
    
    if config.AI_PROVIDER == "anthropic":
        budget.log_usage(resp.usage.input_tokens, resp.usage.output_tokens)
    
    return provider.extract_text(resp).strip()


def generate_summary(period_label: str, data: dict) -> str:
    """Generate spending summary with recommendations."""
    budget.check_budget_before_call()
    
    summary_prompt = prompts.get_summary_prompt(config.CURRENCY)
    resp = provider.call_model(
        summary_prompt,
        f"Period: {period_label}\nData (JSON):\n{json.dumps(data, indent=2)}",
        max_tokens=400,
    )
    
    if config.AI_PROVIDER == "anthropic":
        budget.log_usage(resp.usage.input_tokens, resp.usage.output_tokens)
    
    return provider.extract_text(resp).strip()
