# AI & Tech News -> YouTube Shorts Automation

Phase 2 implementation of a replay-deterministic, production-stable multi-agent pipeline using LangGraph + SQLite + OpenAI.

## Current Phase

| Phase | Name | Status |
|---|---|---|
| 0 | Bootstrap & Observability | DONE |
| 1 | RSS Discovery | DONE |
| 2 | Theme URL Selection | IN_PROGRESS |
| 3 | Interestingness Ranking | LOCKED |
| 4 | Article Extraction | LOCKED |
| 5 | Script Generation | LOCKED |
| 6 | Validation Loop | LOCKED |
| 7 | Image Generation | LOCKED |
| 8 | TTS & Timing | LOCKED |
| 9 | Video Rendering | LOCKED |

## Phase 2 Scope

Phase 2 adds a Theme URL Selector between RSS discovery and ranking with deterministic replay guarantees and production stability checks.

Implemented:
- Theme validation with strict allowed values: `AI` or `Tech`.
- Selector consumes up to 50 normalized `rss_items`.
- Model-based theme scoring with strict JSON schema response contract.
- Deterministic retry policy:
  - 1 retry on malformed/invalid model response.
  - Deterministic keyword-based fallback if retry also fails.
- Deterministic ordering policy:
  - score descending
  - tie-break by `published_at` descending
  - then canonical URL ascending
- Stability policy for live-model variance:
  - replay/test mode uses deterministic mocked/recorded scores
  - production behavior is validated with repeated-run overlap checks (high-overlap subset expectation)
- Cardinality policy:
  - input `< 25` -> return all (policy warning metric)
  - input `25..35` -> return all
  - input `> 35` -> return top 30
- Handoff contract:
  - selected subset written to `state.ranked_items` (reused pre-ranking field)
- Selector artifact output:
  - `outputs/theme_selected_urls.json`

Not implemented in Phase 2:
- Phase 3 interestingness ranking criteria logic.
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

# Override theme for this run (CLI flag, allowed: AI or Tech)
python main.py --theme Tech

# Override max articles for this run (CLI flag)
python main.py --max-articles-per-run 30

# Override both in one command
python main.py --theme AI --max-articles-per-run 30

# Default theme can still be configured in configs/pipeline.yaml
# Example in YAML:
# theme: "Tech"
```

## Runtime Output

Expected outputs after a successful run:
- `outputs/state.json`
- `outputs/rss_items.json`
- `outputs/theme_selected_urls.json`
- `outputs/metadata.json`

## Key Configs

From `configs/pipeline.yaml`:
- `theme: "AI"` (allowed: `AI`, `Tech`)
- `max_articles_per_run: 50`
- `phase2_selector.model: "gpt-4.1-mini"`
- `phase2_selector.target_count: 30`
- `phase2_selector.lower_bound: 25`
- `phase2_selector.upper_bound: 35`
- `phase2_selector.tie_break_policy: "published_at_desc_then_canonical_url_asc"`
- `phase2_selector.deterministic.temperature: 0.0`
- `phase2_selector.deterministic.top_p: 1.0`

From `configs/openai.yaml`:
- `api_key_env_var: "OPENAI_API_KEY"`
- `models.theme_selector: "gpt-4.1-mini"`

## Project Structure

```text
VideoGeneration/
|-- .env
|-- .env.example
|-- agents/
|   |-- rss_collector.py
|   |-- theme_url_selector.py
|   |-- relevance_ranker.py
|   |-- article_extractor.py
|   |-- script_writer.py
|   |-- script_validator.py
|   |-- image_generator.py
|   |-- tts_generator.py
|   |-- video_renderer.py
|   `-- reporter.py
|-- core/
|-- graphs/
|-- configs/
|-- requirements/
|-- tests/
|-- outputs/
`-- main.py
```
