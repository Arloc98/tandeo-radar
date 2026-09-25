"""Offline tests for scripts/eval_extractor.py.

The extractor and the LLM are mocked; nothing here touches the disk outside tmp_path
or the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from app.extractor import ExtractionResult
from app.models import Announcement, TandeoEvent

REPO = Path(__file__).resolve().parent.parent

# Accented alcaldia/colonia names are the normal case in this project.
LABELS_001 = [
    {
        "alcaldia": "Benito Juárez",
        "colonias": ["Del Valle", "Narvarte"],
        "start": "2026-09-25T08:00:00-06:00",
        "end": "2026-09-26T08:00:00-06:00",
        "kind": "suspension",
    }
]

LABELS_002 = [
    {
        "alcaldia": "Álvaro Obregón",
        "colonias": ["Nápoles", "Florida"],
        "start": "2026-09-26T06:00:00-06:00",
        "end": "2026-09-26T18:00:00-06:00",
        "kind": "tandeo",
    }
]

# Prediction for 002: misses colonia Florida, starts 45 min late (inside the +/-60 min
# tolerance), ends 2 hours late (outside it), everything else right.
PREDICTIONS_002 = [
    {
        "alcaldia": "Álvaro Obregón",
        "colonias": ["Nápoles"],
        "start": "2026-09-26T06:45:00-06:00",
        "end": "2026-09-26T20:00:00-06:00",
        "kind": "tandeo",
    }
]


def _load_script():
    path = REPO / "scripts" / "eval_extractor.py"
    spec = importlib.util.spec_from_file_location("eval_extractor_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script(tmp_path, monkeypatch):
    module = _load_script()
    eval_dir = tmp_path / "eval"
    eval_dir.mkdir()
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    monkeypatch.setattr(module, "EVAL_DIR", eval_dir)
    monkeypatch.setattr(module, "RUNS_DIR", runs_dir)
    return module


def _write_pair(eval_dir: Path, stem: str, text: str, labels: list[dict]) -> None:
    (eval_dir / f"{stem}.txt").write_text(text, encoding="utf-8")
    (eval_dir / f"{stem}.json").write_text(
        json.dumps(labels, ensure_ascii=False), encoding="utf-8"
    )


def _fake_extract(responses: dict[str, tuple[list[dict], int]]):
    """Stand in for extractor.extract, keyed by announcement title (the file name)."""

    def _extract(announcement: Announcement) -> ExtractionResult:
        events, discarded = responses[announcement.title]
        parsed = [TandeoEvent(source_url=announcement.url, **e) for e in events]
        return ExtractionResult(parsed, discarded)

    return _extract


def _report(runs_dir: Path) -> dict:
    written = sorted(runs_dir.glob("eval-*.json"))
    assert written, "expected runs/eval-<timestamp>.json to be written"
    return json.loads(written[0].read_text(encoding="utf-8"))


def test_empty_eval_directory_exits_one_without_a_table(script, capsys):
    code = script.main([])

    assert code == 1
    out = capsys.readouterr().out
    assert "eval" in out.lower()
    assert "| field |" not in out, "an empty set must not read as a result"
    assert list(script.RUNS_DIR.glob("eval-*.json")) == []


def test_two_synthetic_pairs_score_each_field(script, monkeypatch, capsys):
    _write_pair(script.EVAL_DIR, "001", "aviso sintetico 001", LABELS_001)
    _write_pair(script.EVAL_DIR, "002", "aviso sintetico 002", LABELS_002)
    monkeypatch.setattr(
        script.extractor,
        "extract",
        _fake_extract({"001.txt": (LABELS_001, 0), "002.txt": (PREDICTIONS_002, 2)}),
    )

    code = script.main([])
    out = capsys.readouterr().out

    assert code == 0
    assert "| field | precision | recall |" in out
    assert "discarded" in out.lower()

    report = _report(script.RUNS_DIR)
    total = report["total"]["fields"]
    assert total["alcaldia"]["precision"] == 1.0
    assert total["alcaldia"]["recall"] == 1.0
    assert total["colonias"]["precision"] == 1.0
    assert total["colonias"]["recall"] == 0.75, "Florida is missed in 002"
    assert total["start"]["recall"] == 1.0, "45 min drift is inside the tolerance"
    assert total["end"]["precision"] == 0.5, "2 h drift is outside the tolerance"
    assert total["end"]["recall"] == 0.5
    assert total["kind"]["recall"] == 1.0

    per_file = {f["stem"]: f for f in report["files"]}
    assert per_file["001"]["discarded"] == 0
    assert per_file["002"]["discarded"] == 2
    assert report["discarded_total"] == 2
    assert report["time_tolerance_minutes"] == 60


def test_malformed_label_file_is_reported_and_the_run_continues(
    script, monkeypatch, capsys
):
    _write_pair(script.EVAL_DIR, "001", "aviso bueno", LABELS_001)
    (script.EVAL_DIR / "002.txt").write_text("aviso con etiqueta rota", encoding="utf-8")
    (script.EVAL_DIR / "002.json").write_text("{ esto no es json", encoding="utf-8")
    monkeypatch.setattr(
        script.extractor, "extract", _fake_extract({"001.txt": (LABELS_001, 0)})
    )

    code = script.main([])
    out = capsys.readouterr().out

    assert code == 0
    assert "002" in out, "the broken file must be named"
    report = _report(script.RUNS_DIR)
    assert [f["stem"] for f in report["files"]] == ["001"]
    assert report["label_errors"][0]["stem"] == "002"


def test_tier_flag_reaches_the_llm_call(script, monkeypatch):
    _write_pair(script.EVAL_DIR, "001", "aviso", LABELS_001)
    seen: list[str | None] = []

    def _complete(system, user, **kwargs):
        seen.append(kwargs.get("tier"))
        return {"events": [dict(LABELS_001[0])]}

    monkeypatch.setattr(script.llm, "complete_json", _complete)

    assert script.main([]) == 0
    assert script.main(["--tier", "reasoning"]) == 0

    assert seen == ["fast", "reasoning"]
