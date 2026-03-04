# AI & Tech News -> YouTube Shorts Automation

Phase 5 Script Generation runtime for a replay-deterministic multi-agent pipeline using LangGraph + SQLite + OpenAI.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | DONE |
| 2 | Theme URL Selection | DONE |
| 3 | Interestingness Ranking | DONE |
| 4 | Article Extraction | DONE |
| 5 | Script Generation | IN PROGRESS |
| 6 | Validation Loop | LOCKED |
| 7 | Image Generation | LOCKED |
| 8 | TTS & Timing | LOCKED |
| 9 | Video Rendering | LOCKED |
| 10 | Production Hardening | LOCKED |

## Phase 5 Scope

Phase 5 adds live Script Generation runtime plus config/prompt/schema contracts while keeping later phases locked.

Implemented:
- Live LLM-backed script generation runtime in `agents/script_writer.py`.
- Strict pipeline config validation for `phase5_script_writer` in `configs/pipeline.yaml`.
- Phase 5 runtime metadata in `configs/pipeline.yaml` (`phase: 5`, `phase_name: "Script Generation"`).
- Active OpenAI model mapping for `models.script_writer` in `configs/openai.yaml`.
- Phase 5 system prompt contract in `prompts/script_writer/system.txt` enforcing strict schema-shaped JSON output.
- `script_writer` node persists `outputs/script.json` and stores the same payload in `state["script_json"]`.

Not implemented yet (future phases):
- Validation loop, image generation, TTS, and video rendering.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/base.txt
pip install -r requirements/phase1.txt
pip install -r requirements/phase2.txt
pip install -r requirements/phase3.txt
pip install -r requirements/phase4.txt
pip install -r requirements/phase5.txt
```

`requirements/phase3.txt`, `requirements/phase4.txt`, and `requirements/phase5.txt` currently introduce no additional third-party packages.

Optional dev tools:

```bash
pip install -r requirements/dev.txt
```

## Environment

Copy `.env.example` to `.env` and set:

- `OPENAI_API_KEY` (required from Phase 2 onward)

The pipeline auto-loads `.env` from project root using `python-dotenv`.

## Run

```bash
python main.py

# Override theme for this run (allowed: AI, Tech)
python main.py --theme Tech

# Override max articles for this run
python main.py --max-articles-per-run 30

# Override RSS feed start index for this run
# (0-based; if omitted, deterministic rotation is used)
python main.py --rss-feed-start-index 2

# Override all in one command
python main.py --theme AI --max-articles-per-run 30
python main.py --theme AI --max-articles-per-run 30 --rss-feed-start-index 2
```

## Runtime Output

Expected outputs after a successful run:
- `outputs/state.json`
- `outputs/rss_items.json`
- `outputs/theme_selected_urls.json`
- `outputs/ranked_items.json`
- `outputs/selection.json`
- `outputs/ranking_criteria_report.json`
- `outputs/article.json`
- `outputs/script.json`
- `outputs/article_raw.html` (only when full extraction is allowed and succeeds)
- `outputs/metadata.json`

Phase 5 script contract:
- `agents/script_writer.py` emits `outputs/script.json` and mirrors it in `state["script_json"]` inside `outputs/state.json`.

## Key Configs

From `configs/pipeline.yaml`:
- `theme: "AI"` (allowed: `AI`, `Tech`)
- `max_articles_per_run: 50`
- `output_dir: "outputs"`
- `database_path: "data/db/app.sqlite"`
- `phase2_selector.*`
- `phase3_ranker.*`
- `phase5_script_writer.*`

From `configs/rss_feeds.yaml`:
- each feed must define `scrape_policy` as one of:
  - `full_scrape_allowed`
  - `metadata_only`

## Source Access Policy

Policy behavior across implemented phases:
- Phase 1 persists `scrape_policy` in SQLite and propagates it into state/artifacts.
- Phase 2 and Phase 3 preserve policy metadata while selecting/ranking.
- Phase 4 enforces extraction gate with deterministic metadata-only fallback when blocked or unresolved.

Policy field appears in:
- `outputs/rss_items.json`
- `outputs/theme_selected_urls.json`
- `outputs/ranked_items.json`
- `outputs/selection.json`
- `outputs/article.json`

## Project Structure

```text
VideoGeneration/
|-- agents/
|   |-- article_extractor.py
|   |-- relevance_ranker.py
|   |-- rss_collector.py
|   |-- script_writer.py
|   `-- theme_url_selector.py
|-- core/
|   |-- model_retry.py
|   |-- config/
|   |-- persistence/
|   `-- state.py
|-- graphs/
|   `-- news_to_video_graph.py
|-- configs/
|   |-- openai.yaml
|   |-- pipeline.yaml
|   `-- rss_feeds.yaml
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
|-- tests/
|   |-- test_model_retry.py
|   |-- test_phase1_exit_criteria.py
|   |-- test_phase2_theme_selector.py
|   |-- test_phase3_relevance_ranker.py
|   |-- test_phase4_article_extractor.py
|   |-- test_phase5_script_writer.py
|   `-- test_source_policy_contract.py
|-- prompts/
|-- schemas/
|-- render/
|-- data/
|-- outputs/
`-- main.py
```
