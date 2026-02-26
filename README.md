# AI & Tech News -> YouTube Shorts Automation

Phase 1 implementation of a deterministic multi-agent pipeline skeleton using LangGraph + SQLite.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | IN_PROGRESS (reopened) |
| 2 | Relevance Ranking | LOCKED |
| 3 | Article Extraction | LOCKED |
| 4 | Script Generation | LOCKED |
| 5 | Validation Loop | LOCKED |
| 6 | Image Generation | LOCKED |
| 7 | TTS & Timing | LOCKED |
| 8 | Video Rendering | LOCKED |
| 9 | Production Hardening | LOCKED |

## Phase 1 Scope

Phase 1 is responsible only for deterministic RSS discovery and persistence of candidate news items.

Implemented:
- Strict config loading for:
  - `configs/rss_feeds.yaml`
  - `configs/openai.yaml`
  - `configs/pipeline.yaml`
- Phase-aware environment validation (Phase 1 requires no API secrets).
- Idempotent SQLite initialization for:
  - `rss_items`
  - `runs`
  - `artifacts`
- RSS ingestion with deterministic behavior:
  - Request timeout: `10s`.
  - Fixed user-agent: `VideoGenerationPhase1RSSCollector/1.0`.
  - Deterministic feed rotation by UTC date (`rss_feed_rotation_basis: utc_date`).
  - URL canonicalization and title normalization.
  - Deduplication by canonical URL first, then normalized title hash.
  - Deduplication against rows already in DB.
- Retention and inventory-aware fetch decision:
  - Cleanup first: delete `rss_items` older than `rss_retention_days` (default `7`).
  - If post-cleanup inventory is `> rss_skip_fetch_threshold` (default `200`), skip HTTP fetch and load top items from DB.
- Collection cap:
  - `max_articles_per_run` default is `50`.
  - Runtime override supported via CLI argument `--max-articles-per-run`.
- Deterministic candidate ordering:
  - Sort by `published_at` descending (missing dates last), then `source`, `title`, `url`.
- Runtime logs for feed order and attempts:
  - Rotated feed traversal order.
  - Per-feed attempt index/source/url.
  - Per-feed success/failure.
- Output artifacts:
  - `outputs/state.json`
  - `outputs/rss_items.json`
  - `outputs/metadata.json`

Not implemented in Phase 1:
- Embeddings/ranking model calls.
- Article scraping.
- LLM script generation.
- Image generation.
- TTS generation.
- Final video rendering.

## End-to-End Behavior (Phase 1)

For each run, the RSS collector executes in this order:

1. Load configs and open SQLite DB.
2. Resolve target cap:
   - Start from `pipeline.max_articles_per_run` (default `50`).
   - If `--max-articles-per-run N` is provided, use `N` for this run only.
3. Run retention cleanup (`rss_retention_days`).
4. Count post-cleanup inventory.
5. Decide fetch path:
   - If inventory `> rss_skip_fetch_threshold`, skip network fetch and read DB items for ranking.
   - Else fetch feeds in rotated deterministic order.
6. While fetching:
   - Continue on individual feed failures.
   - Stop early once cap is reached.
7. Sort collected items deterministically.
8. Insert newly discovered normalized rows in `rss_items`.
9. Populate state + metrics/flags.
10. Fail run only if resulting candidate set is empty.

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
Phase 1 runs without `.env` and without secrets.

## Run

Default run (uses `max_articles_per_run` from config, default `50`):

```bash
python main.py
```

Run with custom max articles for this execution only:

```bash
python main.py --max-articles-per-run 1
```

Validation rule:
- `--max-articles-per-run` must be an integer `>= 1`.

## Runtime Output

Expected results after a successful run:
- `data/db/app.sqlite` exists and schema is initialized.
- `outputs/state.json` is written.
- `outputs/rss_items.json` is written.
- `outputs/metadata.json` is written.
- `rss_items` inserts include only new deduplicated candidates.

Console logs include:
- Feed rotation order used for search.
- Feed attempt sequence (`attempt X/Y`).
- Feed success with entry count.
- Feed failure with traceback.
- Fetch-skip decision when threshold is hit.

## Failure and Collection Semantics

- Partial feed failures are tolerated when at least one valid item is produced.
- Collector fails only when final `rss_items` for the run is empty.
- When feeds are exhausted before the target cap, fewer items are returned and metrics indicate target not reached.
- When skip-fetch path is active, no feed HTTP requests are made in that run.

## Key Configs (Phase 1)

From `configs/pipeline.yaml`:
- `max_articles_per_run: 50`
- `rss_skip_fetch_threshold: 200`
- `rss_retention_days: 7`
- `rss_feed_rotation_basis: "utc_date"`
- `database_path: "data/db/app.sqlite"`
- `output_dir: "outputs"`

From `configs/rss_feeds.yaml`:
- Ordered list of feeds (`name`, `url`) used as base list before deterministic rotation.

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
