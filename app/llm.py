"""Nebius Token Factory client.

Nemotron models on Token Factory are reasoning models: the answer may arrive in
`reasoning_content` with an empty `content`, and tool calls are unreliable.
So we ask for plain JSON and parse whichever field has it.
"""
import json
import logging
import re
from typing import Any

import httpx
from openai import OpenAI

from . import config

logger = logging.getLogger(__name__)

_client = None


class TruncatedAnswer(Exception):
    """The model ran out of tokens before emitting a complete answer."""


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(base_url=config.NEBIUS_BASE_URL, api_key=config.NEBIUS_API_KEY)
    return _client


def extract_json(text: str):
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # reasoning may precede the answer: scan for the last parseable JSON value
    dec = json.JSONDecoder()
    found, i = None, 0
    while i < len(text):
        if text[i] in "[{":
            try:
                obj, end = dec.raw_decode(text, i)
                found, i = obj, end  # keep last top-level value, skip its insides
                continue
            except json.JSONDecodeError:
                pass
        i += 1
    if found is None:
        raise ValueError("No JSON found in model output")
    return found


def _is_bad_request(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 400
    # OpenAI SDK wrappers also surface HTTP 400 as these concrete types.
    status = getattr(exc, "status_code", None)
    return status == 400


def complete_json(
    system: str,
    user: str,
    max_tokens: int = 4000,
    tier: str = "fast",
    schema: dict[str, Any] | None = None,
    think: bool = False,
    temperature: float = 0.0,
):
    if tier not in config.NEBIUS_MODELS:
        raise ValueError(
            f"unknown model tier {tier!r}; expected one of {sorted(config.NEBIUS_MODELS)}"
        )

    params: dict[str, Any] = {
        "model": config.NEBIUS_MODELS[tier],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    if schema is not None:
        params["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "extraction",
                "strict": True,
                "schema": schema,
            },
        }
    if not think:
        params["extra_body"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }

    try:
        resp = client().chat.completions.create(**params)
    except Exception as exc:
        if not _is_bad_request(exc):
            raise
        logger.warning(
            "API rejected structured/thinking parameters (HTTP 400); "
            "retrying once without them"
        )
        params.pop("response_format", None)
        params.pop("extra_body", None)
        resp = client().chat.completions.create(**params)

    choice = resp.choices[0]
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        raise TruncatedAnswer("Model answer was truncated (finish_reason=length)")

    msg = choice.message
    content = msg.content or ""
    if not content.strip():
        content = getattr(msg, "reasoning_content", "") or ""

    return extract_json(content)
