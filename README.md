# AI & Tech News -> YouTube Shorts Automation

Phase 4 implementation of a replay-deterministic multi-agent pipeline using LangGraph + SQLite + OpenAI.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | DONE |
| 2 | Theme URL Selection | DONE |
| 3 | Interestingness Ranking | DONE |
| 4 | Article Extraction | IN PROGRESS |
| 5 | Script Generation | LOCKED |
| 6 | Validation Loop | LOCKED |
| 7 | Image Generation | LOCKED |
| 8 | TTS & Timing | LOCKED |
| 9 | Video Rendering | LOCKED |

## Phase 4 Scope

Phase 4 extracts clean structured article content from the Phase 3 winner URL and enforces source policy gating.

Implemented:
- Source policy gate before extraction:
  - `metadata_only` -> hard block, no HTML fetch attempt.
  - `full_scrape_allowed` -> fetch and extract cleaned content.
- Policy resolution order:
  - ranked item metadata
  - rss item metadata
  - DB fallback by URL
  - fail-closed fallback to `metadata_only`.
- HTML extraction pipeline:
  - fetch raw HTML for allowed sources
  - remove blocked containers (`nav`, `footer`, `script`, ad-like containers)
  - normalize title/author/published date/paragraphs deterministically.
- Artifact contract:
  - `outputs/article.json` always
  - `outputs/article_raw.html` only when full extraction succeeds.
- Safety boundary:
  - raw HTML is never written into `state["article"]`.
  - downstream receives only cleaned `article` payload.

Not implemented in Phase 4:
- Script generation and validation loop.
- Image generation.
- TTS.
- Video rendering.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements/base.txt
pip install -r requirements/phase1.txt
pip install -r requirements/phase2.txt
pip install -r requirements/phase3.txt
pip install -r requirements/phase4.txt
```

`requirements/phase3.txt` and `requirements/phase4.txt` currently introduce no additional third-party packages.

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
- `outputs/article_raw.html` (only when full extraction is allowed and succeeds)
- `outputs/metadata.json`

## Key Configs

From `configs/pipeline.yaml`:
- `theme: "AI"` (allowed: `AI`, `Tech`)
- `max_articles_per_run: 50`
- `output_dir: "outputs"`
- `database_path: "data/db/app.sqlite"`
- `phase2_selector.*`
- `phase3_ranker.*`

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
|   `-- test_source_policy_contract.py
|-- prompts/
|-- schemas/
|-- render/
|-- data/
|-- outputs/
`-- main.py
```
