"""Live probe of the LLM extraction path.

Reads data/sample_announcements.json, sends each announcement through the
structured extraction path, and prints what the API accepted and returned.
"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, extractor, llm


class _ProbeClient:
    """Wraps the real OpenAI client to record calls, latency and token usage."""

    def __init__(self, real_client):
        self.real_client = real_client
        self.calls: list[dict] = []
        self.last_response = None
        self.total_latency = 0.0

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        start = time.perf_counter()
        resp = self.real_client.chat.completions.create(**kwargs)
        self.total_latency += time.perf_counter() - start
        self.last_response = resp
        return resp


def _usage_count(resp, name: str) -> int | None:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return None
    return getattr(usage, name, None)


def main():
    if not config.NEBIUS_API_KEY:
        print("NEBIUS_API_KEY is not set; the probe needs a live key.")
        raise SystemExit(1)

    announcements_path = Path(__file__).resolve().parent.parent / "data" / "sample_announcements.json"
    announcements = json.loads(announcements_path.read_text(encoding="utf-8"))

    real_client = llm.client()
    probe_client = _ProbeClient(real_client)
    llm.client = lambda: probe_client

    print(f"model: {config.NEBIUS_MODEL_FAST}")
    print("-" * 60)

    for idx, ann in enumerate(announcements, start=1):
        probe_client.calls.clear()
        probe_client.total_latency = 0.0
        probe_client.last_response = None

        try:
            result = llm.complete_json(
                extractor.SYSTEM,
                f"Year context: 2026\nPublished: {ann.get('published')}\n"
                f"Title: {ann.get('title')}\n\n{ann.get('content')}",
                schema=extractor.EVENTS_SCHEMA,
                think=False,
            )
        except llm.TruncatedAnswer as exc:
            print(f"[{idx}] TRUNCATED: {exc}")
            continue
        except Exception as exc:
            print(f"[{idx}] ERROR: {exc}")
            continue

        first_call = probe_client.calls[0] if probe_client.calls else {}
        json_schema_accepted = (
            "response_format" in first_call and len(probe_client.calls) == 1
        )
        thinking_disabled = (
            "extra_body" in first_call
            and first_call["extra_body"].get("chat_template_kwargs", {}).get(
                "enable_thinking"
            )
            is False
        )
        finish = getattr(
            probe_client.last_response.choices[0], "finish_reason", "unknown"
        )
        prompt_tokens = _usage_count(probe_client.last_response, "prompt_tokens")
        completion_tokens = _usage_count(
            probe_client.last_response, "completion_tokens"
        )

        print(f"[{idx}] {ann.get('title')}")
        print(f"  json_schema_accepted: {'yes' if json_schema_accepted else 'no'}")
        print(f"  thinking_disabled: {'yes' if thinking_disabled else 'no'}")
        print(f"  finish_reason: {finish}")
        print(f"  prompt_tokens: {prompt_tokens}")
        print(f"  completion_tokens: {completion_tokens}")
        print(f"  latency_ms: {probe_client.total_latency * 1000:.1f}")
        print(f"  extracted_events: {len(result.get('events', []))}")


if __name__ == "__main__":
    main()
