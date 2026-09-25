import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import config, llm


def _fake_client(calls: list[dict]) -> SimpleNamespace:
    def create(**kwargs):
        calls.append(kwargs)
        message = SimpleNamespace(content='{"ok": true}', reasoning_content="")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _load_list_models():
    path = Path(__file__).resolve().parent.parent / "scripts" / "list_models.py"
    spec = importlib.util.spec_from_file_location("list_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tier_selects_the_pinned_model(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(llm, "client", lambda: _fake_client(calls))
    llm.complete_json("system", "user", tier="fast")
    llm.complete_json("system", "user", tier="reasoning")
    assert calls[0]["model"] == config.NEBIUS_MODEL_FAST
    assert calls[1]["model"] == config.NEBIUS_MODEL_REASONING


def test_default_tier_is_fast(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(llm, "client", lambda: _fake_client(calls))
    llm.complete_json("system", "user")
    assert calls[0]["model"] == config.NEBIUS_MODEL_FAST


def test_unknown_tier_raises(monkeypatch):
    monkeypatch.setattr(llm, "client", lambda: _fake_client([]))
    with pytest.raises(ValueError):
        llm.complete_json("system", "user", tier="turbo")


def test_config_pins_the_verified_ids(monkeypatch):
    monkeypatch.delenv("NEBIUS_MODEL_FAST", raising=False)
    monkeypatch.delenv("NEBIUS_MODEL_REASONING", raising=False)
    reloaded = importlib.reload(config)
    assert reloaded.NEBIUS_MODEL_FAST == "nvidia/Nemotron-3_5-Lightning"
    assert reloaded.NEBIUS_MODEL_REASONING == "nvidia/nemotron-3-super-120b-a12b"
    assert reloaded.NEBIUS_MODELS == {"fast": reloaded.NEBIUS_MODEL_FAST,
                                      "reasoning": reloaded.NEBIUS_MODEL_REASONING}


def test_legacy_single_model_variable_is_gone():
    assert not hasattr(config, "NEBIUS_MODEL")


def test_list_models_flags_missing_pins():
    mod = _load_list_models()
    present = list(config.NEBIUS_MODELS.values()) + ["nvidia/Nemotron-3-Ultra-550b-a55b"]
    assert mod.missing_pinned(present) == []
    missing = mod.missing_pinned(["nvidia/Nemotron-3-Ultra-550b-a55b"])
    assert {tier for tier, _ in missing} == {"fast", "reasoning"}


def test_select_nvidia_ids_is_case_insensitive():
    mod = _load_list_models()
    ids = ["NVIDIA/Nemotron-3-Ultra-550b-a55b", "meta/llama-3",
           "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"]
    assert mod.select_nvidia_ids(ids) == sorted([ids[0], ids[2]])
