# AI & Tech News -> YouTube Shorts Automation

Phase 3 implementation of a replay-deterministic, production-stable multi-agent pipeline using LangGraph + SQLite + OpenAI.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | DONE |
| 2 | Theme URL Selection | DONE |
| 3 | Interestingness Ranking | DONE |
| 4 | Article Extraction | LOCKED |
| 5 | Script Generation | LOCKED |
| 6 | Validation Loop | LOCKED |
| 7 | Image Generation | LOCKED |
| 8 | TTS & Timing | LOCKED |
| 9 | Video Rendering | LOCKED |

## Phase 3 Scope

Phase 3 finalizes interestingness ranking from the Phase 2 subset and selects exactly one final URL.

Implemented:
- Phase 3 ranker consumes only `state.ranked_items` from Phase 2.
- Theme enforcement with strict allowed values: `AI` or `Tech`.
- Criteria-based scoring policy with theme-specific rubric labels.
- Model-based scoring with strict JSON schema response contract.
- Deterministic retry policy:
  - 1 retry on malformed/invalid model response.
  - deterministic keyword-rubric fallback if retry also fails.
- Deterministic tie-break policy:
  - score descending
  - `published_at` descending
  - URL ascending
- Exactly-one selection contract:
  - non-empty subset -> first ranked URL becomes `selected_url`
  - empty subset -> deterministic placeholder URL, `selection_count = 0`
- Stability policy:
  - controlled-variance overlap threshold `>= 0.9`
  - threshold sourced from `phase3_ranker.stability.min_overlap_ratio`
- Model/policy observability:
  - model name, temperature, top_p, prompt version
  - criteria policy version and tie-break policy
  - retry/fallback metadata, token usage, latency

Not implemented in Phase 3:
- Article extraction/scraping.
- Script generation/validation.
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
```

`requirements/phase3.txt` intentionally introduces no additional third-party package beyond Phase 2.

Optional dev tools:

```bash
pip install -r requirements/dev.txt
```

## Environment

Copy `.env.example` to `.env` and set:

- `OPENAI_API_KEY` (required from Phase 2 onward)

The pipeline auto-loads `.env` from the project root using `python-dotenv`.

## Run

```bash
python main.py

# Override theme for this run (allowed: AI, Tech)
python main.py --theme Tech

# Override max articles for this run
python main.py --max-articles-per-run 30

# Override both in one command
python main.py --theme AI --max-articles-per-run 30
```

## Runtime Output

Expected outputs after a successful run:
- `outputs/state.json`
- `outputs/rss_items.json`
- `outputs/theme_selected_urls.json`
- `outputs/ranked_items.json`
- `outputs/selection.json`
- `outputs/ranking_criteria_report.json`
- `outputs/metadata.json`

## Key Configs

From `configs/pipeline.yaml`:
- `theme: "AI"` (allowed: `AI`, `Tech`)
- `max_articles_per_run: 50`
- `phase2_selector.*` (Theme URL Selection)
- `phase3_ranker.model: "gpt-4.1-mini"`
- `phase3_ranker.prompt_version: "phase3-interestingness-ranker-v1"`
- `phase3_ranker.criteria_policy_version: "phase3-interestingness-policy-v1"`
- `phase3_ranker.target_selection_count: 1`
- `phase3_ranker.tie_break_policy: "score_desc_then_published_at_desc_then_url_asc"`
- `phase3_ranker.deterministic.temperature: 0.0`
- `phase3_ranker.deterministic.top_p: 1.0`
- `phase3_ranker.stability.min_overlap_ratio: 0.9`

From `configs/openai.yaml`:
- `api_key_env_var: "OPENAI_API_KEY"`
- `models.theme_selector: "gpt-4.1-mini"`
- `models.interestingness_ranker: "gpt-4.1-mini"`

## Source Access Policy

Each feed in `configs/rss_feeds.yaml` must declare `scrape_policy`:
- `full_scrape_allowed`
- `metadata_only`

Example:

```yaml
feeds:
  - name: Wired
    url: https://www.wired.com/feed/rss
    scrape_policy: metadata_only
  - name: TechCrunch
    url: https://techcrunch.com/feed/
    scrape_policy: full_scrape_allowed
```

Policy behavior:
- Phase 1 persists `scrape_policy` in SQLite (`rss_items.scrape_policy`) and propagates it into state and artifacts.
- Phase 2 and Phase 3 preserve policy as transport metadata; ranking logic is unchanged.
- Phase 4 enforces a hard block when selected policy is `metadata_only` and returns a deterministic metadata-only article payload.

Policy field appears in:
- `outputs/rss_items.json`
- `outputs/theme_selected_urls.json`
- `outputs/ranked_items.json`
- `outputs/selection.json`

## Project Structure

```text
VideoGeneration/
|-- agents/
|   |-- article_extractor.py
|   |-- model_retry.py
|   |-- relevance_ranker.py
|   |-- rss_collector.py
|   `-- theme_url_selector.py
|-- core/
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
|   `-- test_source_policy_contract.py
|-- prompts/
|-- schemas/
|-- render/
|-- data/
|-- outputs/
`-- main.py
```
