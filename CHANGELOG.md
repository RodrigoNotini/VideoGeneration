# CHANGELOG

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
