# CHANGELOG

## 0.2.1 - Phase 1 Robustness and Failure Finalization Patch

### What Changed
- Hardened RSS parsed date handling in `agents/rss_collector.py`:
  - Invalid `published_parsed` / `updated_parsed` values no longer crash collection.
  - Collector safely falls back to string date parsing or empty `published_at`.
- Added dedicated dependency error path:
  - `RSSCollectorDependencyError` is raised for missing `feedparser` and is no longer swallowed as a generic feed failure.
- Extended metrics flags:
  - Added `rss_feeds_exhausted_before_target` to explicitly signal cap-not-reached because all feeds were attempted.
- Updated `main.py` run lifecycle:
  - Added `try/except/finally` around pipeline execution.
  - Failure runs now finalize deterministic metadata with `status="failed"`.
  - Failure runs persist run metadata and `metadata` artifact when DB is available.
  - DB connection is always closed in `finally`.

### Why
- Fix four confirmed Phase 1 defects affecting robustness, diagnostics, and failure-path observability.

### Expected Impact
- Single malformed feed entry date cannot abort the entire run.
- Missing `feedparser` dependency is surfaced as actionable root cause.
- Metrics now distinguish target-not-reached due to feed exhaustion.
- Failed runs produce `outputs/metadata.json` and persisted failure run records while cleaning up DB connection deterministically.

### Validation Method
- Execute:
  - `python -m unittest discover -s tests -p "test_*.py"`
- Includes new/updated tests for malformed parsed dates, dependency error propagation, feed exhaustion flag, failed-run metadata persistence, and failure-path DB close.

## 0.2.0 - Phase 1 RSS Discovery Layer

### What Changed
- Replaced `agents/rss_collector.py` stub with real RSS discovery logic:
  - Fetches feeds from `configs/rss_feeds.yaml`.
  - Normalizes and canonicalizes entries into deterministic item payloads.
  - Deduplicates by URL first and title hash second, both in-run and against DB.
  - Persists new rows in `rss_items`.
  - Applies stop-search cap from `max_articles_per_run` (default `20`).
  - Adds phase metrics/flags for feed failures, duplicates, target reached, and collection failure.
- Extended persistence helpers in `core/persistence/db.py`:
  - `fetch_existing_rss_keys(...)`
  - `insert_rss_items(...)`
- Updated `main.py` artifact emission:
  - New `outputs/rss_items.json` artifact.
  - New `rss_items` artifact registration in metadata and `artifacts` table.
- Updated `configs/pipeline.yaml` to Phase 1 metadata and added:
  - `max_articles_per_run: 20`
- Extended strict config validation in `core/config/config_loader.py`:
  - `max_articles_per_run` is required and must be integer `>= 1`.
- Updated `requirements/phase1.txt` with:
  - `feedparser`
  - `requests`
- Added Phase 1 tests for collector behavior, DB persistence, and pipeline artifacts.

### Why
- Phase 1 requires real RSS discovery and deterministic persistence/output contracts while preserving Phase 0 graph topology.

### Expected Impact
- Pipeline now discovers real RSS candidates and produces `rss_items.json`.
- Runs tolerate individual feed failures but fail when zero valid items are collected.
- Collection halts once the configured deduplicated target count is reached.

### Validation Method
- Execute unit + integration tests under `tests/`:
  - RSS normalization/dedup/sorting behavior.
  - DB insertion idempotency and duplicate handling.
  - End-to-end pipeline artifact and persistence assertions with mocked feed responses.

## 0.1.2 - Config Loader Package Placement

### What Changed
- Moved strict config loader module:
  - `config_loader.py` -> `core/config/config_loader.py`
- Updated Phase 0 entrypoint import in `main.py` to use:
  - `from core.config.config_loader import ConfigError, load_all_configs`
- Updated architecture tree in `CONTEXT.md` to reflect the new module location.

### Why
- Keep Python config logic under `core/config/` and reserve `configs/` for YAML data files.

### Expected Impact
- No runtime behavior change.
- Cleaner module organization and import surface for config concerns.

### Validation Method
- Repository-wide search confirms no runtime imports from root `config_loader`.
- Import sanity check: `from core.config.config_loader import ConfigError, load_all_configs`.

## 0.1.1 - Core Domain Package Reorganization

### What Changed
- Refactored root runtime modules into a domain-oriented internal package:
  - `state.py` -> `core/state.py`
  - `db.py` -> `core/persistence/db.py`
  - `env_validation.py` -> `core/config/env_validation.py`
  - `utils.py` -> `core/common/utils.py`
- Added package markers:
  - `core/__init__.py`
  - `core/common/__init__.py`
  - `core/config/__init__.py`
  - `core/persistence/__init__.py`
- Updated all internal imports in `main.py`, `agents/*`, `graphs/*`, and moved modules to use `core.*` paths.

### Why
- Group core runtime contracts and infrastructure by domain ownership.
- Keep `PipelineState` and persistence/config/common helpers under a shared `core` boundary instead of root-level modules.

### Expected Impact
- No runtime behavior change.
- Clearer internal architecture and import surface (`core.state`, `core.persistence.db`, `core.config.env_validation`, `core.common.utils`).
- Root compatibility modules are intentionally not kept.

### Validation Method
- Repository-wide search confirms no stale imports from `state`, `db`, `env_validation`, or `utils` root modules.
- Smoke test executed with `python main.py` to validate runtime wiring, output artifacts, and database persistence paths.

## 0.1.0 - Phase 0 Bootstrap Skeleton

### What Changed
- Created the full Phase 0 directory structure and placeholder artifacts.
  - New files not listed in `CONTEXT.md`: `.env.example`, `prompts/script_writer/system.txt`, `prompts/validator/system.txt`, `render/templates/v1/template_manifest.json`, `outputs/.gitkeep`, `data/db/.gitkeep`, `agents/__init__.py`, `graphs/__init__.py`.
- Added strict config loading for `configs/rss_feeds.yaml`, `configs/openai.yaml`, and `configs/pipeline.yaml`.
  - New file not listed in `CONTEXT.md`: `config_loader.py`.
- Added phase-aware environment validation (Phase 0 allows zero secrets).
  - New file not listed in `CONTEXT.md`: `env_validation.py`.
- Implemented idempotent SQLite initialization for `rss_items`, `runs`, and `artifacts`.
  - New file not listed in `CONTEXT.md`: `db.py`.
- Implemented deterministic state contract and stub agent pipeline.
  - New file not listed in `CONTEXT.md`: `state.py`.
- Added reporter skeleton with deterministic run metadata and metrics.
  - No additional file outside the original `CONTEXT.md` tree for this topic.
- Added runnable entrypoint that writes `outputs/state.json` and `outputs/metadata.json`.
  - New files not listed in `CONTEXT.md`: `main.py`, `utils.py`.

### Why
- Establish a deterministic, observable baseline before enabling functional agents.

### Expected Impact
- Repository can run a full placeholder pipeline end-to-end without external services.

### Validation Method
- Execute `python main.py`.
- Confirm DB creation and metadata generation.
- Re-run to verify idempotent DB initialization and deterministic artifact content.
