"""Verify the model IDs pinned in app/config.py against the live account.

Token Factory retires serverless models without notice or redirection (three left on
31 Aug), so this script does not discover IDs: it checks that the tiers we pin still
exist and lists every NVIDIA/Nemotron model the account can see. Run it before a demo;
it exits non-zero when a pinned model is gone.

Usage: python scripts/list_models.py
"""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config  # noqa: E402  (import after the sys.path fix for `python scripts/...`)


def list_model_ids(base_url: str, api_key: str) -> list[str]:
    """GET {base_url}models, returning every model id the account can see."""
    resp = httpx.get(base_url.rstrip("/") + "/models",
                     headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0)
    resp.raise_for_status()
    return [m["id"] for m in resp.json()["data"]]


def select_nvidia_ids(ids: list[str]) -> list[str]:
    """IDs mentioning nemotron or nvidia, case-insensitively."""
    return sorted(i for i in ids if "nemotron" in i.lower() or "nvidia" in i.lower())


def missing_pinned(ids: list[str]) -> list[tuple[str, str]]:
    """(tier, model_id) pairs pinned in config but absent from ids."""
    return [(t, m) for t, m in sorted(config.NEBIUS_MODELS.items()) if m not in ids]


def main() -> int:
    if not config.NEBIUS_API_KEY:
        print("NEBIUS_API_KEY is not set. Add it to .env or the environment.", file=sys.stderr)
        return 1
    try:
        ids = list_model_ids(config.NEBIUS_BASE_URL, config.NEBIUS_API_KEY)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        print(f"Could not list models from {config.NEBIUS_BASE_URL}: {exc}", file=sys.stderr)
        return 1

    nvidia = select_nvidia_ids(ids)
    print(f"{len(nvidia)} NVIDIA/Nemotron model(s) in the account:")
    for mid in nvidia:
        print(f"  {mid}")

    missing = missing_pinned(ids)
    if missing:
        for tier, mid in missing:
            print(f"MISSING: tier {tier!r} pins {mid!r}, which is not in the account.",
                  file=sys.stderr)
        return 1
    print("OK: every pinned model tier is present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
