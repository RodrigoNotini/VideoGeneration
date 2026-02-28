# AI & Tech News â†’ YouTube Shorts Automation System
## MAS Architecture using LangGraph + OpenAI + MoviePy

---

# 1. Project Vision

This project implements a production-grade Multi-Agent System (MAS) that automatically generates short-form AI & Tech news videos in English for YouTube Shorts.

The system must:

- Collect AI & Tech news via RSS feeds
- Select theme-aligned URLs before ranking
- Rank articles by AI & Tech interestingness criteria
- Extract and normalize article content
- Generate a structured short-form script using OpenAI models
- Generate narration using OpenAI Text-to-Speech (TTS)
- Generate up to 3 AI images per video (always generated)
- Render a vertical 9:16 video using MoviePy
- Produce 2 videos per day reliably

The system must be:

- Modular
- Deterministic
- Schema-driven
- Observable
- Version-controlled
- Cost-aware

All architectural changes must include documented justification.

---

# 2. Editorial Strategy

## 2.1 Content Focus

Primary niche:

- Artificial Intelligence
- Big Tech
- AI Regulation
- AI Infrastructure
- Semiconductors
- Robotics
- Emerging Technologies
- Startups

Content must:

- Be factual
- Avoid speculation
- Cite the original source
- Be concise
- Be optimized for retention

---

# 3. RSS Sources (Official Discovery Layer)

These RSS feeds must be used for news discovery.

## Core AI & Tech Feeds

- TechCrunch  
  https://techcrunch.com/feed/

- The Verge  
  https://www.theverge.com/rss/index.xml

- Wired  
  https://www.wired.com/feed/rss

- MIT Technology Review  
  https://www.technologyreview.com/feed/

- Ars Technica  
  http://feeds.arstechnica.com/arstechnica/index

- VentureBeat â€“ AI  
  https://venturebeat.com/category/ai/feed/

## Business & Technology Economy

- CNBC â€“ Technology  
  https://www.cnbc.com/id/19854910/device/rss/rss.html

- Reuters â€“ Technology  
  https://www.reutersagency.com/feed/?best-topics=technology&post_type=best

- Bloomberg â€“ Technology  
  https://feeds.bloomberg.com/technology/news.rss

## Infrastructure & Semiconductor Focus

- Semiconductor Engineering  
  https://semiengineering.com/feed/

---

# 4. System Architecture (LangGraph MAS)

## 4.1 High-Level Pipeline

RSS â†’ Theme Selection â†’ Ranking â†’ Extraction â†’ Script â†’ Validation â†’ Image Generation â†’ TTS â†’ Render â†’ Output

---

# 5. LangGraph State Object

The shared state must contain:

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

State contract note:

- No new selector-specific state field is introduced in this reform.
- `ranked_items` is currently reused for the Phase 2 selector output (pre-ranking subset), then reused by ranking for ordered results.
- A dedicated intermediate selector field may be introduced in a later implementation update.

---

# 6. Agent Definitions

## Agent 1 â€” RSS Collector

Responsibilities:

- Fetch RSS feeds
- Normalize entries
- Deduplicate by URL and title hash
- Store candidates in database
- Enforce per-feed `scrape_policy` (`full_scrape_allowed` or `metadata_only`) from `configs/rss_feeds.yaml`
- Run retention cleanup at collector start and delete rows older than 7 days
- Evaluate post-cleanup DB inventory and skip network fetch when inventory is > 200
- Load deterministic DB-backed candidates when fetch is skipped so theme selection still runs
- Apply deterministic feed balancing with rotated start index derived from UTC date
- Persist and propagate `scrape_policy` for every candidate into DB, state, and downstream artifacts

RSS Discovery Invariants:

- Cleanup executes before threshold evaluation on every run.
- No `rss_items` row may remain in DB for more than 7 days.
- Feed traversal order must be deterministic for a fixed UTC date and feed list.
- `rss_items.scrape_policy` must be synchronized to current feed config by source name on collector start.

---

## Agent 2 â€” Theme URL Selector

Responsibilities:

- Consume RSS candidate list from Agent 1 (up to 50 URLs)
- Apply user-selected theme (`AI` or `Tech`)
- Use a low-cost model to select a subset in the 25â€“35 range
- Forward selected subset to ranking

---

## Agent 3 â€” Interestingness Ranker

Responsibilities:

- Receive the 25â€“35 theme-selected candidates from Agent 2
- Score and rank candidates with a dedicated ranking model (non-embedding)
- Select one final article (`selected_url`) from the ranked list
- Apply theme-specific criteria

AI News â€” Top 5 Criteria:

- Human stakes
- Novelty / "First ever"
- Controversy or tension
- Visual or demonstrable proof
- Speculation about the future

Tech News â€” Top 5 Criteria:

- Immediate real-world impact
- Credibility of the source
- Simplicity of the core idea
- Timeliness / news hook
- Contrarianism

Ranking invariants:

- Criteria set must match selected theme (`AI` or `Tech`)
- Ranking and tie-break behavior must be deterministic for identical inputs
- Embedding-based semantic similarity is not used in this phase

---

## Agent 4 â€” Article Extractor

Responsibilities:

- Resolve selected source policy from ranked item metadata (fallback lookup by URL)
- Hard-block full extraction for `metadata_only` sources
- Return deterministic metadata-only structured payload when blocked
- Scrape article HTML only for `full_scrape_allowed` sources
- Remove ads and navigation text
- Normalize into structured format
- Extract clean paragraphs

Extraction Policy Invariants:

- No full HTML fetch attempt is allowed when `scrape_policy = metadata_only`.
- Policy-gated runs must expose explicit metrics/flags for observability.

---

## Agent 5 â€” Script Writer (OpenAI LLM)

Responsibilities:

- Generate strong hook
- Create 6â€“10 scenes
- Limit to 3 image prompts
- Optimize pacing for 35â€“60 seconds
- Include source line
- Output must strictly follow JSON schema

No free-form text allowed.

---

## Agent 6 â€” Script Validator

Checks:

- Valid JSON
- Duration between 35â€“60 seconds
- Maximum 3 images
- Scene count between 6â€“10
- Text length constraints

If validation fails:
Return structured correction instructions.

---

## Agent 7 â€” Image Generator (OpenAI)

Policy:

- Always generate images
- Maximum 3 unique prompts
- Vertical framing (9:16)
- No embedded text in images

---

## Agent 8 â€” TTS Generator (OpenAI)

Responsibilities:

- Convert narration into speech
- Generate single combined audio file
- Measure real duration
- Adjust timing if necessary

---

## Agent 9 â€” Video Renderer (MoviePy)

Specifications:

- Resolution: 1080x1920
- Vertical format (9:16)
- Deterministic template (v1)
- Fade transitions
- Subtitle overlay
- Static layout system

Renderer must not invent content.

---

## Agent 10 â€” Reporter

Responsibilities:

- Log all pipeline stages
- Save metadata
- Track execution time
- Store model versions
- Track approximate cost

---

# 7. OpenAI Model Usage Policy

OpenAI models are used for:

- Script generation
- Image generation
- Text-to-Speech

Rules:

- Always enforce JSON schema
- Prompts must be versioned
- Token usage must be minimized
- Model names must be logged
- No raw HTML sent to LLM
- Context packing required

---

# 8. Script Schema (Strict Contract)

The script must contain:

- video_title
- source_line
- hook
- scenes (array)
- cta

Constraints:

- 35â€“60 seconds total
- 6â€“10 scenes
- Maximum 3 image prompts
- Concise narration
- Hook shorter than 15 words

---

# 9 . Structural Organization & Production Hardening

VideoGeneration/
â”œâ”€â”€ IMPLEMENTATION_PLAN.md
â”œâ”€â”€ CONTEXT.md
â”œâ”€â”€ CHANGELOG.md
â”œâ”€â”€ README.md
â”œâ”€â”€ main.py                       # Phase 0 entrypoint (stub deterministic pipeline)
â”œâ”€â”€ core/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ state.py                  # State contract and deterministic initial state
â”‚   â”œâ”€â”€ common/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â””â”€â”€ utils.py              # Deterministic utility helpers
â”‚   â”œâ”€â”€ config/
â”‚   â”‚   â”œâ”€â”€ __init__.py
â”‚   â”‚   â”œâ”€â”€ config_loader.py      # Strict config loader
â”‚   â”‚   â””â”€â”€ env_validation.py     # Phase-aware env validation
â”‚   â””â”€â”€ persistence/
â”‚       â”œâ”€â”€ __init__.py
â”‚       â””â”€â”€ db.py                 # SQLite initialization and persistence helpers
â”œâ”€â”€ requirements.txt              # Agregador opcional (referencia requirements/*.txt)
â”œâ”€â”€ .env                          # OpenAI key (root, auto-loaded by python-dotenv; not versioned)
â”œâ”€â”€ .env.example                  # Env vars esperadas por fase
â”œâ”€â”€ .gitignore
â”‚
â”œâ”€â”€ requirements/                 # Source of truth das dependÃªncias
â”‚   â”œâ”€â”€ base.txt                  # Runtime mÃ­nimo (Phase 0)
â”‚   â”œâ”€â”€ dev.txt                   # Ferramentas de desenvolvimento/teste
â”‚   â”œâ”€â”€ phase1.txt                # RSS Discovery
â”‚   â”œâ”€â”€ phase2.txt                # Theme URL Selection
â”‚   â”œâ”€â”€ phase3.txt                # Interestingness Ranking (Criteria-Based)
â”‚   â”œâ”€â”€ phase4.txt                # Article Extraction
â”‚   â”œâ”€â”€ phase5.txt                # Script Generation (LLM)
â”‚   â”œâ”€â”€ phase6.txt                # Validation Loop
â”‚   â”œâ”€â”€ phase7.txt                # Image Generation
â”‚   â”œâ”€â”€ phase8.txt                # TTS & Audio
â”‚   â”œâ”€â”€ phase9.txt                # Video Rendering
â”‚   â””â”€â”€ phase10.txt               # Production Hardening / Scheduler
â”‚                 
â”‚
â”œâ”€â”€ configs/
â”‚   â”œâ”€â”€ rss_feeds.yaml
â”‚   â”œâ”€â”€ openai.yaml
â”‚   â””â”€â”€ pipeline.yaml
â”‚
â”œâ”€â”€ prompts/
â”‚   â”œâ”€â”€ script_writer/
â”‚   â”‚   â””â”€â”€ system.txt
â”‚   â””â”€â”€ validator/
â”‚       â””â”€â”€ system.txt
â”‚
â”œâ”€â”€ schemas/
â”‚   â”œâ”€â”€ article_schema.json
â”‚   â””â”€â”€ script_schema.json
â”‚
â”œâ”€â”€ agents/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â”œâ”€â”€ rss_collector.py
â”‚   â”œâ”€â”€ theme_url_selector.py
â”‚   â”œâ”€â”€ relevance_ranker.py
â”‚   â”œâ”€â”€ article_extractor.py
â”‚   â”œâ”€â”€ script_writer.py
â”‚   â”œâ”€â”€ script_validator.py
â”‚   â”œâ”€â”€ image_generator.py
â”‚   â”œâ”€â”€ tts_generator.py
â”‚   â”œâ”€â”€ video_renderer.py
â”‚   â””â”€â”€ reporter.py
â”‚
â”œâ”€â”€ graphs/
â”‚   â”œâ”€â”€ __init__.py
â”‚   â””â”€â”€ news_to_video_graph.py
â”‚
â”œâ”€â”€ render/
â”‚   â””â”€â”€ templates/
â”‚       â””â”€â”€ v1/                   # Template versionado
â”‚           â””â”€â”€ template_manifest.json
â”‚
â”œâ”€â”€ outputs/
â”‚   â””â”€â”€ .gitkeep
â”‚
â””â”€â”€ data/
    â””â”€â”€ db/
        â”œâ”€â”€ .gitkeep
        â””â”€â”€ app.sqlite            # Gerado em runtime

---

# 10. Change Transparency Policy (Mandatory)

Every architectural change must include:

- What changed
- Why it changed
- Expected impact
- Validation method
- Version increment

No silent modifications allowed.

All runs must log:

- Prompt version
- Schema version
- Template version
- Model version

---

# 11. Success Criteria

- 2 videos per day automatically generated
- Failure rate below 10%
- 100% schema validation
- Proper audio synchronization
- Clean vertical rendering
- Strong hook and pacing
- Source citation included




