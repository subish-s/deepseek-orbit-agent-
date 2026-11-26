"""Chat, agent, and OpenAI-compatible calls all use the direct account HTTP client.

There is no API-key provider. Requests go over HTTPS to the account session
in auth_session.json. The password is used only when that token must be refreshed.
"""

import os
import re

from dotenv import load_dotenv

import deepseek_http

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def provider_name() -> str:
    return "direct"


def describe() -> dict:
    return {
        "provider": "direct",
        "model": "account-chat",
        "transport": "https",
        "agent": True,
        "openai_compatible": "/v1/chat/completions",
    }


def complete(
    prompt: str,
    *,
    session_name: str = "daily",
    thinking: bool | None = None,
    search: bool | None = None,
) -> str:
    try:
        return deepseek_http.complete(
            prompt,
            session_path=_session_path(session_name),
            thinking_enabled=thinking,
            search_enabled=search,
        )
    except Exception as exc:
        message = str(exc)
        if "captcha" in message.lower() or "RISK_" in message:
            raise RuntimeError(
                "The account login was challenged. Open the saved browser profile once "
                "so the device id can refresh, then retry. This app does not solve captchas."
            ) from exc
        raise


def complete_messages(
    messages: list[dict],
    *,
    session_name: str = "daily",
    thinking: bool | None = None,
    search: bool | None = None,
) -> str:
    return complete(_flatten(messages), session_name=session_name, thinking=thinking, search=search)


def _flatten(messages: list[dict]) -> str:
    parts = []
    for message in messages:
        role = str(message.get("role") or "user").upper()
        content = message.get("content") or ""
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        parts.append(f"{role}:\n{content}")
    return "\n\n".join(parts)


def _session_path(session_name: str) -> str | None:
    if session_name in ("", "daily"):
        return None
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_name)[:48]
    folder = os.path.join(PROJECT_ROOT, "agent_sessions")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{safe}.json")
