# AI & Tech News -> YouTube Shorts Automation

Phase 1 implementation of a deterministic Multi-Agent System skeleton using LangGraph + SQLite.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | DONE (validated) |
| 2 | Relevance Ranking | LOCKED |
| 3 | Article Extraction | LOCKED |
| 4 | Script Generation | LOCKED |
| 5 | Validation Loop | LOCKED |
| 6 | Image Generation | LOCKED |
| 7 | TTS & Timing | LOCKED |
| 8 | Video Rendering | LOCKED |
| 9 | Production Hardening | LOCKED |

## Phase 1 Capabilities

- Strict config loading for:
  - `configs/rss_feeds.yaml`
  - `configs/openai.yaml`
  - `configs/pipeline.yaml`
- Phase-aware environment validation (Phase 0 and 1 require no secrets).
- Idempotent SQLite initialization:
  - `rss_items`
  - `runs`
  - `artifacts`
- Real RSS discovery from configured feeds with:
  - 10s request timeout + deterministic user-agent.
  - Partial feed failure handling (continue on feed error).
  - Deterministic normalization (`id`, `title`, `url`, `published_at`, `title_hash`).
  - Deduplication by canonical URL first, title hash second.
  - Deduplication against existing database rows.
  - Stop-search cap via `max_articles_per_run` (default `20`).
- Deterministic sorted RSS candidates:
  - Sort key: `published_at` desc (empty dates last), then `source`, `title`, `url`.
- Output artifacts:
  - `outputs/state.json`
  - `outputs/rss_items.json`
  - `outputs/metadata.json`

## Phase 1 Completion Check (2026-02-25)

Phase 1 exit criteria are satisfied in the current codebase/execution baseline:

- RSS fetching verified by automated tests (`tests/test_phase1_exit_criteria.py`).
- Deduplication verified (canonical URL + title hash logic, including tracking parameter removal).
- Deterministic ordering verified (`published_at` desc, then `source`, `title`, `url`).
- Dependencies isolated for this phase (`requirements/phase1.txt` contains only RSS-related additions).
- README aligned with Phase 1 scope and status.

## Explicitly Not Implemented in Phase 1

- Embeddings/ranking model calls
- Article scraping
- LLM generation
- Image generation APIs
- TTS APIs
- Real video rendering

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/base.txt
pip install -r requirements/phase1.txt
```

Optional dev tools:

```bash
pip install -r requirements/dev.txt
```

## Environment

Copy `.env.example` to `.env` if needed.  
Phase 1 runs without `.env` and without any secrets.

## Run

```bash
python main.py
```

Expected results:

- `data/db/app.sqlite` is created automatically.
- `outputs/state.json` is generated.
- `outputs/rss_items.json` is generated.
- `outputs/metadata.json` is generated.
- `rss_items` rows are inserted only for newly discovered deduplicated candidates.

## Failure and Collection Behavior

- If one or more feeds fail but at least one valid item is collected, the run continues.
- The collector fails only when all feeds produce zero valid items for the run.
- Collection stops as soon as `max_articles_per_run` deduplicated items are reached.
- If feeds are exhausted first, fewer items are returned and metrics indicate target not reached.

## Project Structure

```text
VideoGeneration/
|-- agents/
|   |-- article_extractor.py
|   |-- image_generator.py
|   |-- relevance_ranker.py
|   |-- reporter.py
|   |-- rss_collector.py
|   |-- script_validator.py
|   |-- script_writer.py
|   |-- tts_generator.py
|   |-- video_renderer.py
|   `-- __init__.py
|-- configs/
|   |-- openai.yaml
|   |-- pipeline.yaml
|   `-- rss_feeds.yaml
|-- core/
|   |-- common/
|   |   |-- utils.py
|   |   `-- __init__.py
|   |-- config/
|   |   |-- config_loader.py
|   |   |-- env_validation.py
|   |   `-- __init__.py
|   |-- persistence/
|   |   |-- db.py
|   |   `-- __init__.py
|   |-- state.py
|   `-- __init__.py
|-- data/db/
|   |-- .gitkeep
|   `-- app.sqlite
|-- graphs/
|   |-- news_to_video_graph.py
|   `-- __init__.py
|-- outputs/
|   |-- .gitkeep
|   |-- metadata.json
|   |-- rss_items.json
|   `-- state.json
|-- prompts/
|   |-- script_writer/system.txt
|   `-- validator/system.txt
|-- render/templates/v1/
|   `-- template_manifest.json
|-- requirements/
|   |-- base.txt
|   |-- dev.txt
|   |-- phase1.txt
|   |-- phase2.txt
|   |-- phase3.txt
|   |-- phase4.txt
|   |-- phase5.txt
|   |-- phase6.txt
|   |-- phase7.txt
|   |-- phase8.txt
|   `-- phase9.txt
|-- schemas/
|   |-- article_schema.json
|   `-- script_schema.json
|-- .env.example
|-- .gitignore
|-- CHANGELOG.md
|-- CONTEXT.md
|-- IMPLEMENTATION_PLAN.md
|-- README.md
|-- requirements.txt
`-- main.py
```
