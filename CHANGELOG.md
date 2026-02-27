# CHANGELOG
## 0.2.5 - Phase 2 Criteria Reframe: Deterministic Replay + Production Stability
### What Changed
- Updated global determinism policy in `IMPLEMENTATION_PLAN.md` to distinguish:
  - deterministic replay/test behavior, and
  - bounded-variance production behavior with explicit stability criteria.
- Updated `IMPLEMENTATION_PLAN.md` Phase 2 exit criteria:
  - replaced strict identical-output requirement for live model runs with:
    - deterministic replay subset checks, and
    - repeated-run overlap stability checks under controlled score variance.
- Added Phase 2 test coverage in `tests/test_phase2_theme_selector.py`:
  - `test_stability_overlap_under_controlled_score_variance`.
- Updated `README.md` Phase 2 description to reflect replay determinism and production stability.

### Why
- Phase 2 uses an LLM-based selector, where strict bit-for-bit production determinism is not a realistic contract for live calls.
- Replay determinism keeps CI/debugging reproducible while stability checks keep production quality bounded and observable.

### Expected Impact
- More realistic and enforceable Phase 2 contract for LLM-backed selection.
- Better alignment between planning docs, operational docs, and executable tests.

### Validation Method
- Run:
  - `python -m unittest discover -s tests -p "test_*.py"`

## 0.2.4 - Documentation Reform: Criteria-Based Agent 3 Ranking
### What Changed
- Reformulated `Agent 3` in documentation from embedding-based relevance ranking to criteria-based interestingness ranking.
- Updated `IMPLEMENTATION_PLAN.md` Phase 3 from `Relevance Ranking` to `Interestingness Ranking`.
- Replaced OpenAI embedding references in Phase 3 scope with a dedicated non-embedding model policy.
- Added explicit theme criteria blocks used by Agent 3:
  - `AI News` Top 5: Human stakes; Novelty/"First ever"; Controversy/tension; Visual proof; Future speculation.
  - `Tech News` Top 5: Immediate impact; Source credibility; Simplicity; Timeliness/news hook; Contrarianism.
- Updated architecture mapping notes in `CONTEXT.md` (`phase3.txt` comment and model-usage policy) to remove embedding ranking references.

### Why
- Align documentation with the updated architecture where Agent 3 selects the single most interesting article from the 25–35 Phase 2 subset using explicit editorial criteria.

### Expected Impact
- Clear implementation contract for Agent 3 behavior and model choice.
- Better consistency between theme selection (Phase 2) and final article selection (Phase 3).
- Removal of ambiguity caused by outdated embedding-based ranking language.

### Validation Method
- Manual consistency check across `IMPLEMENTATION_PLAN.md`, `CONTEXT.md`, and `CHANGELOG.md` for:
  - No active references to OpenAI embeddings for Agent 3 ranking.
  - Phase 3 wording aligned to criteria-based interestingness ranking.
  - Presence of both AI and Tech Top-5 criteria blocks in Agent 3 documentation.

### Scope Clarification
- Documentation/specification update only.
- No runtime code behavior changed in this step.

## 0.2.3 - Documentation Reform: Selector Phase Reindex
### What Changed
- Added documentation for a new `Phase 2 — Theme URL Selection` between RSS discovery and ranking.
- Reindexed downstream implementation phases from `2..9` to `3..10` in planning and architecture docs.
- Updated workflow and agent ordering in context documentation to `RSS → Theme Selection → Ranking → Extraction → Script → Validation → Image Generation → TTS → Render → Output`.
- Added explicit state-contract note that selector output currently reuses `ranked_items` pre-ranking, with a dedicated intermediate field deferred to a future implementation step.

### Why
- Reflect the architecture change that inserts a theme selector before ranking and remove ambiguity in sequencing and ownership.

### Expected Impact
- Clearer planning and execution order for upcoming implementation work.
- Consistent phase and agent numbering across documentation artifacts.
- Reduced risk of misaligned implementation against outdated docs.

### Validation Method
- Manual consistency check across `IMPLEMENTATION_PLAN.md`, `CONTEXT.md`, and `CHANGELOG.md` for:
  - Phase naming and numbering alignment.
  - Agent numbering/order alignment with pipeline flow.
  - Final phase consistently documented as `Phase 10 — Production Hardening`.
  - Absence of legacy references to the old phase-2 ranking heading.

### Scope Clarification
- Documentation/specification update only.
- No runtime code behavior changed in this step.

## 0.2.2 - Phase 1 adjustments 
### What Changed
- Reopened Phase 1 and set it to `IN_PROGRESS` in `IMPLEMENTATION_PLAN.md`.
- Updated Phase 1 collector runtime in `agents/rss_collector.py`:
  - Run-start retention cleanup before any fetch/skip decision.
  - Skip network fetch when post-cleanup DB inventory is strictly `> 200`.
  - Deterministic DB hydration path for skip mode (feeds are not requested).
  - Increased effective cap usage to `max_articles_per_run = 50`.
  - Deterministic feed rotation via `start_index = f(utc_date) % total_feeds` and rotated traversal order.
  - Added metrics/flags for retention deletions, threshold skip, and feed rotation index/basis.
- Extended persistence API in `core/persistence/db.py`:
  - `delete_rss_items_older_than(...)`
  - `count_rss_items(...)`
  - `fetch_rss_items_for_ranking(...)`
- Extended strict pipeline config validation in `core/config/config_loader.py` and config values in `configs/pipeline.yaml`:
  - `max_articles_per_run: 50`
  - `rss_skip_fetch_threshold: 200`
  - `rss_retention_days: 7`
  - `rss_feed_rotation_basis: utc_date`
- Expanded tests in `tests/test_phase1_exit_criteria.py` for retention, strict threshold semantics, cap at 50, and deterministic feed rotation.
- Synchronized docs in `CHANGELOG.md`, `CONTEXT.md`, `IMPLEMENTATION_PLAN.md`, and `README.md`.

### Why
- Reduce redundant network calls when DB already has enough recent inventory.
- Improve deterministic feed balancing across runs.
- Enforce RSS freshness by ensuring DB rows older than one week are removed.
- Keep plan/architecture/operations docs aligned after reopening Phase 1.

### Expected Impact
- Warm DB runs will often skip external feed requests and continue directly to ranking.
- Feed start point rotates deterministically by UTC date, improving coverage distribution.
- RSS DB freshness is bounded to one week.
- Per-run item budget is now 50 for both fetch and skip paths.

### Validation Method
- Targeted checks implemented in `tests/test_phase1_exit_criteria.py`.
- Full test command:
  - `python -m unittest discover -s tests -p "test_*.py"`
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
