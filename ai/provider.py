"""
ai/provider.py - AI provider abstraction (Anthropic vs Ollama)
"""

import json
import urllib.error
import urllib.request

import config


def call_model(system_prompt: str, user_prompt: str, *, max_tokens: int):
    """
    Call the configured AI provider (Anthropic or Ollama).
    Returns response in normalized format.
    """
    if config.AI_PROVIDER == "anthropic":
        return _call_anthropic(system_prompt, user_prompt, max_tokens)
    elif config.AI_PROVIDER == "ollama":
        return _call_ollama(system_prompt, user_prompt, max_tokens)
    else:
        raise RuntimeError(f"Unsupported AI_PROVIDER '{config.AI_PROVIDER}'. Use 'anthropic' or 'ollama'.")


def _call_anthropic(system_prompt: str, user_prompt: str, max_tokens: int):
    """Call Anthropic API."""
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise RuntimeError("AI_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set.")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=config.MODEL,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return resp


def _call_ollama(system_prompt: str, user_prompt: str, max_tokens: int):
    """Call local Ollama API."""
    payload = {
        "model": config.OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    url = f"{config.OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama request failed for {url}: {exc}") from exc

    data = json.loads(body)
    return {"content": [{"text": data["message"]["content"]}]}


def extract_text(response) -> str:
    """Extract plain text from any provider response."""
    if isinstance(response, str):
        return response.strip()

    if isinstance(response, dict):
        # Handle Ollama format
        message = response.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
        # Handle normalized format
        content = response.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if isinstance(item.get("text"), str):
                        return item["text"].strip()
                    if isinstance(item.get("content"), str):
                        return item["content"].strip()

    # Handle Anthropic object
    content = getattr(response, "content", None)

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        for item in content:
            if hasattr(item, "text"):
                text = getattr(item, "text")
                if isinstance(text, str):
                    return text.strip()
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    return item["text"].strip()
                if isinstance(item.get("content"), str):
                    return item["content"].strip()

    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str):
        return output_text.strip()

    return ""


def get_usage_info(response) -> dict:
    """Extract token usage from Anthropic responses (Ollama doesn't track this)."""
    if hasattr(response, "usage"):
        return {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    return {"input_tokens": 0, "output_tokens": 0}
