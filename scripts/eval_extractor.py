"""Score extractor accuracy against the hand-labelled set in data/eval/.

Each evaluation item is a pair: data/eval/NNN.txt holds the announcement text and
data/eval/NNN.json holds the hand-labelled events (a JSON list of TandeoEvent fields,
without source_url). See data/eval/README.md for the labelling rules.

Prints a markdown report per file and in total, and writes runs/eval-<timestamp>.json.
Exit codes: 0 the set was evaluated, 1 there was nothing to evaluate.
"""
# Historical note: this file originally avoided `from __future__ import annotations`
# because the contract loaded it without registering it in sys.modules, which breaks
# @dataclass under stringified annotations on Python 3.14. The contract fixture now
# registers it, so the restriction is gone and future annotations would be fine here.
import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, extractor, llm
from app.models import Announcement, TandeoEvent

EVAL_DIR: Path = ROOT / "data" / "eval"
RUNS_DIR: Path = ROOT / "runs"

TIME_TOLERANCE = timedelta(minutes=60)
# Labels and the extractor prompt both work in America/Mexico_City (no DST since 2022);
# a naive datetime is read with that offset instead of failing the comparison.
LOCAL_OFFSET = timezone(timedelta(hours=-6))
# Table order is deliberate: alcaldia last, so a wrong alcaldia is the final row.
FIELDS: tuple[str, ...] = ("colonias", "start", "end", "kind", "alcaldia")


class LabelError(Exception):
    """A label file is unreadable; the run continues with the remaining files."""


@dataclass
class Score:
    """Micro-aggregated counts for one field; precision/recall stay undefined if empty."""

    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        total = self.tp + self.fp
        return self.tp / total if total else None

    @property
    def recall(self) -> float | None:
        total = self.tp + self.fn
        return self.tp / total if total else None

    def add(self, other: "Score") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn

    def as_dict(self) -> dict:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
        }


@dataclass
class FileResult:
    stem: str
    labels: int
    predictions: int
    discarded: int
    scores: dict[str, Score]


def load_labels(path: Path) -> list[TandeoEvent]:
    if not path.is_file():
        raise LabelError(f"{path.name} not found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LabelError(f"invalid JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise LabelError("labels must be a JSON list of events")
    events: list[TandeoEvent] = []
    for i, item in enumerate(raw):
        try:
            events.append(TandeoEvent(source_url=f"labels:{path.name}", **item))
        except Exception as exc:
            raise LabelError(f"event {i}: {exc}") from exc
    return events


def _as_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=LOCAL_OFFSET)


def same_moment(a: datetime | None, b: datetime | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(_as_local(a) - _as_local(b)) <= TIME_TOLERANCE


def pair_score(label: TandeoEvent, pred: TandeoEvent) -> int:
    score = 0
    if label.alcaldia == pred.alcaldia:
        score += 2
    if set(label.colonias) & set(pred.colonias):
        score += 2
    if label.kind == pred.kind:
        score += 1
    if same_moment(label.start, pred.start):
        score += 1
    if same_moment(label.end, pred.end):
        score += 1
    return score


def pair_events(
    labels: list[TandeoEvent], predictions: list[TandeoEvent]
) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedy best-score matching; leftovers are scored as unmatched predictions."""
    ranked = sorted(
        (
            (pair_score(label, pred), li, pi)
            for li, label in enumerate(labels)
            for pi, pred in enumerate(predictions)
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    used_labels: set[int] = set()
    used_predictions: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for _, li, pi in ranked:
        if li in used_labels or pi in used_predictions:
            continue
        used_labels.add(li)
        used_predictions.add(pi)
        pairs.append((li, pi))
    unmatched_labels = [i for i in range(len(labels)) if i not in used_labels]
    unmatched_predictions = [
        i for i in range(len(predictions)) if i not in used_predictions
    ]
    return pairs, unmatched_labels, unmatched_predictions


def score_pair(label: TandeoEvent, pred: TandeoEvent) -> dict[str, Score]:
    scores = {f: Score() for f in FIELDS}
    for f in ("alcaldia", "kind"):
        if getattr(label, f) == getattr(pred, f):
            scores[f].tp += 1
        else:
            scores[f].fp += 1
            scores[f].fn += 1
    label_set, pred_set = set(label.colonias), set(pred.colonias)
    both = label_set & pred_set
    scores["colonias"].tp += len(both)
    scores["colonias"].fp += len(pred_set - both)
    scores["colonias"].fn += len(label_set - both)
    for f in ("start", "end"):
        if same_moment(getattr(label, f), getattr(pred, f)):
            scores[f].tp += 1
        else:
            scores[f].fp += 1
            scores[f].fn += 1
    return scores


def score_unmatched(event: TandeoEvent, *, predicted: bool) -> dict[str, Score]:
    side = "fp" if predicted else "fn"
    scores = {f: Score() for f in FIELDS}
    for f in ("alcaldia", "start", "end", "kind"):
        score = scores[f]
        setattr(score, side, getattr(score, side) + 1)
    colonias = scores["colonias"]
    setattr(colonias, side, getattr(colonias, side) + len(event.colonias))
    return scores


def merge(target: dict[str, Score], addition: dict[str, Score]) -> None:
    for f in FIELDS:
        target[f].add(addition[f])


def evaluate_file(
    labels: list[TandeoEvent], predictions: list[TandeoEvent]
) -> dict[str, Score]:
    scores = {f: Score() for f in FIELDS}
    pairs, unmatched_labels, unmatched_predictions = pair_events(labels, predictions)
    for li, pi in pairs:
        merge(scores, score_pair(labels[li], predictions[pi]))
    for li in unmatched_labels:
        merge(scores, score_unmatched(labels[li], predicted=False))
    for pi in unmatched_predictions:
        merge(scores, score_unmatched(predictions[pi], predicted=True))
    return scores


def format_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def print_table(scores: dict[str, Score]) -> None:
    print("| field | precision | recall | tp | fp | fn |")
    print("| --- | --- | --- | --- | --- | --- |")
    for f in FIELDS:
        s = scores[f]
        print(
            f"| {f} | {format_metric(s.precision)} | {format_metric(s.recall)}"
            f" | {s.tp} | {s.fp} | {s.fn} |"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate extractor accuracy against the labelled set in data/eval/."
    )
    parser.add_argument(
        "--tier",
        choices=("fast", "reasoning"),
        default="fast",
        help="model tier used to run the extractor over the same set",
    )
    args = parser.parse_args(argv)

    txt_files = sorted(EVAL_DIR.glob("*.txt")) if EVAL_DIR.is_dir() else []
    if not txt_files:
        print(f"Nothing to evaluate: no announcement files (*.txt) in {EVAL_DIR}.")
        print("See data/eval/README.md for the NNN.txt / NNN.json pairing.")
        return 1

    print("Tandeo Radar - extractor eval")
    print(f"tier: {args.tier} (model: {config.NEBIUS_MODELS[args.tier]})")
    print(f"pairs found: {len(txt_files)}")

    results: list[FileResult] = []
    label_errors: list[tuple[str, str]] = []
    extract_errors: list[tuple[str, str]] = []
    totals = {f: Score() for f in FIELDS}
    total_discarded = 0

    # extractor.extract() does not forward a tier, and app/ is off limits here, so the
    # chosen tier is injected as complete_json's default for the duration of the run.
    original_complete = llm.complete_json

    def complete_with_tier(system, user, *call_args, **call_kwargs):
        call_kwargs.setdefault("tier", args.tier)
        return original_complete(system, user, *call_args, **call_kwargs)

    llm.complete_json = complete_with_tier
    try:
        for txt in txt_files:
            stem = txt.stem
            try:
                labels = load_labels(EVAL_DIR / f"{stem}.json")
            except LabelError as exc:
                label_errors.append((stem, str(exc)))
                continue
            announcement = Announcement(
                url=txt.as_uri(),
                title=txt.name,
                content=txt.read_text(encoding="utf-8"),
            )
            try:
                extracted = extractor.extract(announcement)
            except Exception as exc:
                # A failed call must not masquerade as "the model found no events".
                extract_errors.append((stem, f"{type(exc).__name__}: {exc}"))
                continue
            predictions = list(extracted)
            scores = evaluate_file(labels, predictions)
            results.append(
                FileResult(
                    stem=stem,
                    labels=len(labels),
                    predictions=len(predictions),
                    discarded=extracted.discarded,
                    scores=scores,
                )
            )
            total_discarded += extracted.discarded
            merge(totals, scores)
    finally:
        llm.complete_json = original_complete

    txt_stems = {p.stem for p in txt_files}
    for path in sorted(EVAL_DIR.glob("*.json")):
        if path.stem not in txt_stems:
            label_errors.append((path.stem, f"{path.name} has no matching .txt"))

    print(f"evaluated: {len(results)} | label errors: {len(label_errors)}"
          f" | extraction errors: {len(extract_errors)}")

    if label_errors:
        print("\n## label errors")
        for stem, message in label_errors:
            print(f"- {stem}: {message}")

    if extract_errors:
        print("\n## extraction errors")
        for stem, message in extract_errors:
            print(f"- {stem}: {message}")

    if not results:
        print("\nNo file could be evaluated; see the errors above.")
        return 1

    for res in results:
        print(f"\n## {res.stem}")
        print_table(res.scores)
        print(f"\ndiscarded rows: {res.discarded}")

    print("\n## total")
    print_table(totals)
    print(f"\ndiscarded rows (total): {total_discarded}")

    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tier": args.tier,
        "model": config.NEBIUS_MODELS[args.tier],
        "eval_dir": str(EVAL_DIR),
        "time_tolerance_minutes": int(TIME_TOLERANCE.total_seconds() // 60),
        "files": [
            {
                "stem": r.stem,
                "labels": r.labels,
                "predictions": r.predictions,
                "discarded": r.discarded,
                "fields": {f: r.scores[f].as_dict() for f in FIELDS},
            }
            for r in results
        ],
        "label_errors": [
            {"stem": stem, "error": message} for stem, message in label_errors
        ],
        "extraction_errors": [
            {"stem": stem, "error": message} for stem, message in extract_errors
        ],
        "discarded_total": total_discarded,
        "total": {
            "files": len(results),
            "fields": {f: totals[f].as_dict() for f in FIELDS},
        },
    }
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = RUNS_DIR / f"eval-{datetime.now():%Y%m%d-%H%M%S}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
