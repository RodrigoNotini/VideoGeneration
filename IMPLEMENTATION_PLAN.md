# IMPLEMENTATION_PLAN.md
## AI & Tech News → YouTube Shorts Automation System
### Development Roadmap (Phase-Gated Execution)

---

# 0. Execution Principles

This document defines HOW the system is built.  
All architectural contracts, invariants, and policies live in CONTEXT.md.

Rules:
- Do not create new files unless required by CONTEXT.md.
- If a new file is necessary, place it in the most appropriate existing folder.
- Create a new folder only if no existing folder fits, and name it descriptively.
- Keep the project structure clean, modular, and consistent.
- Only one phase may be IN_PROGRESS at a time.
- Future-phase functionality must NOT be implemented early.
- A phase becomes DONE only after all exit criteria are satisfied.
- Once DONE, that phase’s interfaces are frozen.
- Any modification to a DONE phase requires:
  - CHANGELOG entry
  - Justification
  - Validation method
  - Version increment (if applicable)

Determinism Rule (Global):

- Same input state must always produce the same output artifacts.
- Any stochastic component (LLM, ranking ties, etc.) must be explicitly controlled and logged.
- Model parameters affecting output must be versioned and logged.

---

# Documentation Policy (Mandatory)

- `CONTEXT.md` is the architectural contract (what the system is).
- `IMPLEMENTATION_PLAN.md` defines execution sequencing (how the system is built).
- `README.md` is the onboarding and operational document.

Rules for README:

- A minimal `README.md` MUST be created in Phase 0.
- Each phase MUST update `README.md` to reflect:
  - New capabilities added
  - New dependencies introduced
  - New environment variables required
  - Updated execution instructions
- README must always reflect the current Phase Status Board.
- README must not document future-phase functionality prematurely.

---

# Dependency & Environment Management Policy (Mandatory)

Goal: keep installs reproducible and phase-gated, without forcing future-phase dependencies early.

## Requirements layout (source of truth)

Dependencies are managed under `requirements/`:

- `requirements/base.txt` — Phase 0 runtime only (must be sufficient to run Phase 0 end-to-end).
- `requirements/dev.txt` — optional dev/test tooling.
- `requirements/phaseX.txt` — incremental dependencies introduced by Phase X.

## Phase gating rules

A phase MUST NOT:

- require installing dependencies from future phases, or
- import modules that only exist due to future-phase dependencies.

If a phase introduces new third-party libraries, it MUST update `requirements/phaseX.txt`.

## Optional convenience

- `requirements.txt` may exist as a local aggregator, but the source of truth remains `requirements/*.txt`.

## Environment variables policy

- `.env` is never committed.
- `.env.example` must be maintained.
- Env validation is phase-aware:
  - Phase 0 runs with zero secrets.
  - Secrets are enforced only in the phase that actually uses them.

---

# Phase Status Board

| Phase | Name | Status |
|-------|------|--------|
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

---

# Phase 0 — Bootstrap & Observability Skeleton

## Goal

Create minimal runnable pipeline skeleton with logging, config loading, database initialization, stub agents, full folder structure, deterministic state object, and reproducible baseline without enabling future-phase functionality.

## Scope

- Create full folder structure defined in CONTEXT.md.
- Create placeholder files for:
  - agents/*
  - graphs/news_to_video_graph.py
  - schemas/*
  - prompts/*
  - render/templates/v1/*
- Implement config loader (rss_feeds.yaml, openai.yaml, pipeline.yaml).
- Implement SQLite initialization:
  - rss_items
  - runs
  - artifacts
- Implement Reporter skeleton:
  - run_id
  - timestamps
  - phase name
  - version placeholders
  - structured metrics container
- Implement deterministic LangGraph skeleton with stub logic only.
- Define and freeze the LangGraph State Object containing ALL required fields from CONTEXT.md:
  - topic
  - target_platform
  - target_duration_sec
  - rss_items
  - ranked_items
  - selected_url
  - article
  - script_json
  - generated_images
  - narration_audio_path
  - render_output_path
  - metrics
  - version_info
- Stub pipeline must populate all state fields with deterministic placeholder values.
- Create dependency layout:
  - `requirements/base.txt`
  - `requirements/dev.txt`
  - `requirements/phase1.txt` … `requirements/phase9.txt`
- Create `.env.example`.
- Implement phase-aware env validation.
- Create and populate `.gitignore`.
- Create initial `README.md`.

## Explicitly Forbidden

- No RSS fetching.
- No embeddings.
- No scraping.
- No OpenAI calls.
- No TTS.
- No video rendering.
- No failing Phase 0 due to missing secrets.

## Deliverables

- Full directory structure created.
- Config loader implemented.
- DB auto-created with required tables.
- Reporter skeleton implemented.
- Deterministic State Object defined.
- LangGraph stub pipeline runs end-to-end.
- metadata.json generated.
- requirements directory created.
- `.env.example` created.
- `.gitignore` created and populated.
- `README.md` created.

## Exit Criteria
- Config loading validated (no silent defaults)
- State Object contract verified:
- Fresh setup succeeds using only `requirements/base.txt`.
- Phase 0 runs without `.env`.
- DB schema idempotent.
- Deterministic stub output.
- No future-phase imports.
- README accurately reflects current state.

---

# Phase 1 — RSS Discovery Layer

## Goal

Fetch RSS feeds defined in `configs/rss_feeds.yaml`, normalize entries, deduplicate, and persist candidates.

## Scope

- Update `requirements/phase1.txt`.
- Implement rss_collector.py.
- Normalize fields.
- Deduplicate by URL and title hash.
- Persist to DB.
- Generate rss_items.json.

## Exit Criteria

- RSS fetching verified.
- Deduplication verified.
- Deterministic ordering.
- Dependencies isolated.
- README updated accurately.

---

# Phase 2 — Relevance Ranking

## Goal

Rank RSS items by semantic relevance to AI & Tech.

## Scope

- Update `requirements/phase2.txt`.
- Implement relevance_ranker.py.
- Use OpenAI embeddings.
- Ensure deterministic tie-breaking.
- Log embedding model version.

## Deliverables

- ranked_items.json.
- selection.json.

## Exit Criteria

- Deterministic ranking for identical inputs.
- Stable selection behavior.
- Dependencies isolated.
- Model parameters logged.
- README updated accurately.

---

# Phase 3 — Article Extraction

## Goal

Extract clean structured content from selected article.

## Scope

- Update `requirements/phase3.txt`.
- Implement article_extractor.py.
- Extract raw HTML for auditing (`article_raw.html`).
- Produce cleaned structured output (`article.json`).
- Normalize:
  - title
  - author
  - publication date
  - clean paragraph list
- Remove ads/navigation.

IMPORTANT:

- Raw HTML MUST NOT be passed to LLM.
- Only cleaned and size-limited `article.json` may be used downstream.

## Deliverables

- article_raw.html.
- article.json.

## Exit Criteria

- Clean structured schema produced.
- No raw HTML sent downstream.
- Dependencies isolated.
- README updated.

---

# Phase 4 — Script Generation (LLM)

## Goal

Generate structured short-form script strictly following JSON schema.

## Scope

- Update `requirements/phase4.txt`.
- Implement script_writer.py.
- Use ONLY `article.json` as input.
- Enforce strict JSON schema.
- Version prompts.
- Log model name and generation parameters.

## Deliverables

- script.json (single-pass generation only).

## Exit Criteria

- Output strictly matches schema.
- No free-form output.
- Deterministic configuration logged.
- Dependencies isolated.
- README updated.

---

# Phase 5 — Script Validation Loop

## Goal

Validate script and apply deterministic retry loop if necessary.

## Scope

- Update `requirements/phase5.txt`.
- Implement script_validator.py.
- Validate:
  - JSON validity
  - 35–60 sec duration
  - 6–10 scenes
  - Max 3 image prompts
  - Hook length < 15 words
- If invalid:
  - Produce structured correction instructions.
  - Retry deterministically.

## Deliverables

- validation_report.json.
- script_validated.json (or script_final.json).

## Exit Criteria

- 100% schema compliance.
- Deterministic retry behavior.
- Dependencies isolated.
- README updated.

---

# Phase 6 — Image Generation

## Goal

Generate up to 3 vertical AI images.

## Scope

- Update `requirements/phase6.txt`.
- Implement image_generator.py.
- Always generate images.
- Maximum 3 unique prompts.
- Enforce:
  - 9:16 framing
  - No embedded text
- Log image model version.

## Deliverables

- images/ directory.
- images_manifest.json.

## Exit Criteria

- Resolution verified (vertical 9:16).
- Constraints enforced.
- Dependencies isolated.
- README updated.

---

# Phase 7 — TTS & Duration Control

## Goal

Generate narration and verify duration compliance.

## Scope

- Update `requirements/phase7.txt`.
- Implement tts_generator.py.
- Generate single combined narration file.
- Measure real duration.
- Log TTS model version.

## Deliverables

- narration file.
- audio_metrics.json.

## Exit Criteria

- Duration within 35–60 seconds.
- Deterministic audio generation parameters logged.
- Dependencies isolated.
- README updated.

---

# Phase 8 — Video Rendering

## Goal

Render deterministic vertical video using MoviePy template v1.

## Scope

- Update `requirements/phase8.txt`.
- Implement video_renderer.py.
- Use only template v1.
- Enforce:
  - 1080x1920 resolution
  - Static layout
  - Fade transitions
  - Subtitle overlay
- Renderer must not invent content.

## Deliverables

- final.mp4.
- render_manifest.json.

## Exit Criteria

- 1080x1920 verified.
- Deterministic output.
- Dependencies isolated.
- README updated.

---

# Phase 9 — Production Hardening

## Goal

Ensure system stability and production-readiness.

## Scope

- Scheduler integration.
- Cost reports.
- 7-day logging.
- Failure monitoring.
- Budget tracking.

## Deliverables

- Scheduler setup.
- Cost summary reports.
- 7-day run logs.

## Exit Criteria

- ≥ 2 runs/day for 7 consecutive days.
- Failure rate < 10%.
- Budget compliance.
- README reflects production-ready system.

---

# Phase Advancement Protocol

To move to next phase:

1. Confirm all exit criteria met.
2. Update Phase Status Board.
3. Update README.md.
4. Freeze interfaces.
5. Commit changes.
6. Record architectural shifts in CHANGELOG (if applicable).

---

# Codex Invocation Rule

When invoking Codex, always specify:

"We are in Phase X. Implement only the scope defined for this phase in IMPLEMENTATION_PLAN.md. Do not implement future-phase functionality."