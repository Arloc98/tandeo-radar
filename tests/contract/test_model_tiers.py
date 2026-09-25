"""Model tiers and the retirement guardrail.

Token Factory retires models from Serverless without notice and does not redirect the
requests, so `scripts/list_models.py` exists to fail loudly when a pinned ID is gone.

Everything here goes through `main()` and asserts on its exit code and output, never on
internals, so the script stays free to change shape.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

REPO = Path(__file__).resolve().parent.parent.parent
ACCOUNT = [
    "nvidia/Nemotron-3_5-Lightning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/Nemotron-3-Ultra-550b-a55b",
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "meta/llama-3.1-8b",
]


@pytest.fixture
def script():
    """scripts/list_models.py loaded as a module, the way `python scripts/...` runs it."""
    path = REPO / "scripts" / "list_models.py"
    spec = importlib.util.spec_from_file_location("list_models_contract", path)
    module = importlib.util.module_from_spec(spec)
    # Register before executing. Without this, a script combining @dataclass with
    # `from __future__ import annotations` dies with "AttributeError: 'NoneType' object has
    # no attribute '__dict__'" on Python 3.14, because stringified annotations resolve
    # through sys.modules. Dropping future annotations from the script would also work,
    # but the test should not deform the code it measures.
    sys.modules["list_models_contract"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("list_models_contract", None)
        raise
    return module


@pytest.fixture
def offline(script, monkeypatch):
    """Serve a fixed account listing so the contract never touches the network."""
    def _serve(ids=ACCOUNT):
        monkeypatch.setattr(script, "list_model_ids", lambda *a, **kw: list(ids))
    return _serve


def test_exits_zero_and_lists_nvidia_models(script, offline, monkeypatch, capsys):
    monkeypatch.setattr(script.config, "NEBIUS_API_KEY", "fake-key")
    offline()
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "nvidia/Nemotron-3_5-Lightning" in out
    assert "meta/llama-3.1-8b" not in out          # only NVIDIA/Nemotron are listed


def test_exits_one_without_a_key(script, monkeypatch, capsys):
    monkeypatch.setattr(script.config, "NEBIUS_API_KEY", "")
    assert script.main() == 1
    assert "NEBIUS_API_KEY" in capsys.readouterr().err


def test_exits_one_when_a_pinned_model_is_retired(script, offline, monkeypatch, capsys):
    """The guardrail: Token Factory drops models without notice, and that must be loud."""
    monkeypatch.setattr(script.config, "NEBIUS_API_KEY", "fake-key")
    monkeypatch.setattr(script.config, "NEBIUS_MODELS",
                        {"fast": "nvidia/Gone-Yesterday", "reasoning": ACCOUNT[1]})
    offline()
    assert script.main() == 1
    err = capsys.readouterr().err
    assert "nvidia/Gone-Yesterday" in err          # names the model, not just the tier
    assert "fast" in err


def test_exits_one_when_the_account_cannot_be_reached(script, monkeypatch, capsys):
    monkeypatch.setattr(script.config, "NEBIUS_API_KEY", "fake-key")

    def _boom(*a, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(script, "list_model_ids", _boom)
    assert script.main() == 1
    assert "Could not list models" in capsys.readouterr().err


def test_unknown_tier_is_rejected_rather_than_defaulted():
    """A typo in a tier name must not silently run on the fast model."""
    from app import llm

    with pytest.raises(ValueError, match="unknown model tier"):
        llm.complete_json("system", "user", tier="fastest")
