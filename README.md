# AI & Tech News -> YouTube Shorts Automation

Phase 0 bootstrap for a deterministic Multi-Agent System skeleton using LangGraph + SQLite.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | IN PROGRESS |
| 2 | Relevance Ranking | LOCKED |
| 3 | Article Extraction | LOCKED |
| 4 | Script Generation | LOCKED |
| 5 | Validation Loop | LOCKED |
| 6 | Image Generation | LOCKED |
| 7 | TTS & Timing | LOCKED |
| 8 | Video Rendering | LOCKED |
| 9 | Production Hardening | LOCKED |

## Phase 0 Capabilities

- Strict config loading for:
  - `configs/rss_feeds.yaml`
  - `configs/openai.yaml`
  - `configs/pipeline.yaml`
- Phase-aware environment validation (Phase 0 requires no secrets).
- Idempotent SQLite initialization:
  - `rss_items`
  - `runs`
  - `artifacts`
- Deterministic state contract containing all required fields.
- Stub agent pipeline wired in `graphs/news_to_video_graph.py`.
- Reporter skeleton with deterministic run metadata and stage metrics.
- Output artifacts:
  - `outputs/state.json`
  - `outputs/metadata.json`

## Explicitly Not Implemented in Phase 0

- RSS fetching
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
```

Optional dev tools:

```bash
pip install -r requirements/dev.txt
```

## Environment

Copy `.env.example` to `.env` if needed.  
Phase 0 runs without `.env` and without any secrets.

## Run

```bash
python main.py
```

Expected results:

- `data/db/app.sqlite` is created automatically.
- `outputs/state.json` is generated.
- `outputs/metadata.json` is generated.

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
