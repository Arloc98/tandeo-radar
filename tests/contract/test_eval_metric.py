"""The evaluation script and its accuracy metric.

Written against the script's observable behaviour -- its exit code, its stdout, the JSON it
writes -- and not against helper functions, so the implementation stays free to change shape.
"""
from __future__ import annotations

import importlib.util
import sys
import json
from pathlib import Path

import pytest

from app.models import TandeoEvent


REPO = Path(__file__).resolve().parent.parent.parent

LABELLED = [
    {
        "alcaldia": "Benito Juárez",
        "colonias": ["Del Valle", "Narvarte"],
        "start": "2026-09-25T08:00:00-06:00",
        "end": "2026-09-26T08:00:00-06:00",
        "kind": "suspension",
    }
]


@pytest.fixture
def script():
    path = REPO / "scripts" / "eval_extractor.py"
    if not path.is_file():
        pytest.fail("scripts/eval_extractor.py does not exist yet")
    spec = importlib.util.spec_from_file_location("eval_extractor_contract", path)
    module = importlib.util.module_from_spec(spec)
    # Register before executing. Without this, a script combining @dataclass with
    # `from __future__ import annotations` dies with "AttributeError: 'NoneType' object has
    # no attribute '__dict__'" on Python 3.14, because stringified annotations resolve
    # through sys.modules. Dropping future annotations from the script would also work,
    # but the test should not deform the code it measures.
    sys.modules["eval_extractor_contract"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop("eval_extractor_contract", None)
        raise
    return module


@pytest.fixture
def eval_dir(tmp_path, monkeypatch, script):
    """Point the script at a temp eval directory, whatever attribute it uses for it."""
    directory = tmp_path / "eval"
    directory.mkdir()
    for name in ("EVAL_DIR", "EVAL_PATH", "DATA_EVAL"):
        if hasattr(script, name):
            monkeypatch.setattr(script, name, directory)
            break
    else:
        pytest.fail("the script must expose its eval directory as a module attribute")
    return directory


def _write_pair(directory: Path, stem: str, text: str, labels: list[dict]) -> None:
    (directory / f"{stem}.txt").write_text(text, encoding="utf-8")
    (directory / f"{stem}.json").write_text(
        json.dumps(labels, ensure_ascii=False), encoding="utf-8"
    )


def _fake_extract(events: list[dict], discarded: int = 0):
    """Stand in for extractor.extract, returning the real ExtractionResult type."""
    from app.extractor import ExtractionResult

    def _inner(announcement, *a, **kw):
        parsed = [TandeoEvent(source_url=announcement.url, **e) for e in events]
        return ExtractionResult(parsed, discarded)

    return _inner


def test_exits_one_and_explains_when_there_is_nothing_to_evaluate(script, eval_dir, capsys):
    """An empty set must not read as a result. The directory is empty until the team fills it."""
    code = script.main([])
    assert code == 1
    output = (capsys.readouterr().out + capsys.readouterr().err).lower()
    assert "eval" in output


def test_a_perfect_prediction_scores_one(script, eval_dir, monkeypatch, capsys):
    _write_pair(eval_dir, "001", "SACMEX suspende el servicio en Del Valle y Narvarte", LABELLED)
    monkeypatch.setattr(script.extractor, "extract", _fake_extract(LABELLED))

    assert script.main([]) == 0
    out = capsys.readouterr().out
    assert "alcaldia" in out.lower()
    assert "1.0" in out or "100" in out


def test_a_wrong_alcaldia_does_not_score_one(script, eval_dir, monkeypatch, capsys):
    _write_pair(eval_dir, "001", "aviso", LABELLED)
    wrong = [dict(LABELLED[0], alcaldia="Tlalpan")]
    monkeypatch.setattr(script.extractor, "extract", _fake_extract(wrong))

    script.main([])
    out = capsys.readouterr().out
    assert "1.0" not in out.split("alcaldia")[-1][:80], "a wrong alcaldia cannot score 1.0"


def test_discarded_rows_are_reported(script, eval_dir, monkeypatch, capsys):
    """Zero recall from model garbage must not read like zero recall from a wrong colonia."""
    _write_pair(eval_dir, "001", "aviso", LABELLED)
    monkeypatch.setattr(script.extractor, "extract", _fake_extract([], discarded=3))

    script.main([])
    combined = capsys.readouterr()
    assert "3" in (combined.out + combined.err), "the discarded count must be visible"
    assert "discard" in (combined.out + combined.err).lower()


def test_a_malformed_label_file_does_not_abort_the_run(script, eval_dir, monkeypatch, capsys):
    _write_pair(eval_dir, "001", "aviso bueno", LABELLED)
    (eval_dir / "002.txt").write_text("aviso con etiqueta rota", encoding="utf-8")
    (eval_dir / "002.json").write_text("{ esto no es json", encoding="utf-8")
    monkeypatch.setattr(script.extractor, "extract", _fake_extract(LABELLED))

    code = script.main([])
    out = (capsys.readouterr().out + capsys.readouterr().err)
    assert "002" in out, "the broken file must be named"
    assert code is not None, "the run must finish rather than raise"


def test_writes_a_json_report(script, eval_dir, monkeypatch, tmp_path):
    _write_pair(eval_dir, "001", "aviso", LABELLED)
    monkeypatch.setattr(script.extractor, "extract", _fake_extract(LABELLED))
    runs = tmp_path / "runs"
    runs.mkdir()
    for name in ("RUNS_DIR", "RUNS_PATH", "OUT_DIR"):
        if hasattr(script, name):
            monkeypatch.setattr(script, name, runs)
            break
    else:
        pytest.fail("the script must expose its output directory as a module attribute")

    script.main([])
    written = list(runs.glob("eval-*.json"))
    assert written, "expected runs/eval-<timestamp>.json"
    json.loads(written[0].read_text(encoding="utf-8"))


def test_tier_flag_is_accepted(script, eval_dir, monkeypatch, capsys):
    _write_pair(eval_dir, "001", "aviso", LABELLED)
    monkeypatch.setattr(script.extractor, "extract", _fake_extract(LABELLED))
    assert script.main(["--tier", "reasoning"]) == 0
