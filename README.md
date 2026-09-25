# Tandeo Radar

An AI agent that turns scattered water-outage announcements in Mexico City into open,
structured data (JSON / GeoJSON), and tells each household how many days its cistern will
last against the next announced cut.

## The problem

*Tandeo* — intermittent water supply — is how a large part of Mexico City gets its water.
The schedules and the outage notices are published as press releases and social posts,
never as structured data. Nobody can query them, map them, or subscribe to them. Households
find out their tank is empty when the tap runs dry.

The tandeo data that does exist is
[frozen in 2019](https://datos.cdmx.gob.mx/dataset/colonias-con-distribucion-de-agua-por-tandeo-2019).
What does not exist is anything close to real time. Tandeo Radar builds that layer.

## How it works

```
Tavily search  →  Nemotron on Nebius Token Factory  →  TandeoEvent  →  GeoJSON + autonomy
   notices            free text to structured           validated        map and days left
```

1. **Collect** — `app/collector.py` searches official and press sources through Tavily,
   restricted to recent notices, and rejects results that are only a headline.
2. **Extract** — `app/extractor.py` sends the free text to an NVIDIA Nemotron model and gets
   back `TandeoEvent` records: alcaldía, colonias, start, end, kind, confidence.
3. **Map** — `app/geo.py` geocodes colonias with OpenStreetMap Nominatim and exposes them as
   GeoJSON.
4. **Autonomy** — `app/autonomy.py` computes `A = V / Q_out` (tank volume over average daily
   demand) and compares it with the next cut affecting the user's colonia.

### Nebius Token Factory and Nemotron

The extraction layer runs entirely on Token Factory's OpenAI-compatible API, on two tiers:

| Tier | Model | Used for |
|---|---|---|
| `fast` | `nvidia/Nemotron-3_5-Lightning` | every notice, first pass |
| `reasoning` | `nvidia/nemotron-3-super-120b-a12b` | the retry, when the first pass comes back suspiciously empty |

Two things this project learned the hard way, and that the code now handles:

- **Nemotron models are reasoning models.** The answer can arrive in `content` or in
  `reasoning_content`, and Lightning sometimes spills its reasoning into `content` and
  leaves `reasoning_content` empty. `app/llm.py` reads both and never relies on tool calls.
- **Token Factory retires serverless models without notice and does not redirect the
  request.** Model IDs are never hardcoded from memory: `scripts/list_models.py` checks the
  pinned IDs against the live account listing and exits non-zero when one is gone.

A model that returns a valid but empty list is the dangerous case — for a service that tells
people when their water is cut off, "no cuts announced" and "the model call broke" must never
look the same. The extractor runs at temperature 0, flags a suspiciously empty result, and
retries it one tier up.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # add NEBIUS_API_KEY and TAVILY_API_KEY
uvicorn app.main:app --reload
# open http://localhost:8000
pytest -q
```

Without `NEBIUS_API_KEY` the app serves synthetic fixtures, so the UI can be developed
offline. With `OFFLINE=true` it runs the LLM over synthetic announcements from `data/`
instead of live search.

## API

| Method | Path | Description |
|---|---|---|
| POST | `/refresh` | collect → extract → geocode; reports events, discards and errors |
| GET | `/events?alcaldia=&colonia=` | structured outage events |
| GET | `/events.geojson` | the same events as GeoJSON |
| POST | `/autonomy?colonia=` | household autonomy against the next cut |

## Measuring the extractor

`data/eval/` holds real outage notices paired with hand-checked labels — `NNN.txt` with the
announcement text, `NNN.json` with the events it announces.

```bash
python scripts/eval_extractor.py --tier fast
python scripts/eval_extractor.py --tier reasoning
```

It reports precision and recall per field (alcaldía, colonias, start, end within ±60 min,
kind) plus the rows the extractor discarded, per file and in total. A recall of zero with
many discarded rows is a different diagnosis from a recall of zero with none: the first
means the model returned garbage, the second means it got the colonia wrong.

See `data/eval/README.md` for the labelling rules and the provenance of the set.

## What this does not do yet

Stated plainly, because a demo that hides its edges is not useful:

- **No persistence.** State lives in memory, which blocks history, deduplication and
  change detection.
- **The eval set is press coverage, not official bulletins.** Direct SACMEX and alcaldía
  notices mostly come back empty or as unrelated pages. That is a real limit of the
  collection layer.
- **Alcaldía names are compared literally**, so `Magdalena Contreras` against the official
  `La Magdalena Contreras` counts as an error when it is not.
- **Mexico City only.** No hardware, no notifications.

## Data and license

Sample data in `data/sample_*.json` is synthetic and labeled as such. The notices in
`data/eval/` are public press coverage, kept for evaluation.

MIT — see `LICENSE`.
