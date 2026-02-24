# AI & Tech News → YouTube Shorts Automation System
## MAS Architecture using LangGraph + OpenAI + MoviePy

---

# 1. Project Vision

This project implements a production-grade Multi-Agent System (MAS) that automatically generates short-form AI & Tech news videos in English for YouTube Shorts.

The system must:

- Collect AI & Tech news via RSS feeds
- Rank articles by semantic relevance
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

- VentureBeat – AI  
  https://venturebeat.com/category/ai/feed/

## Business & Technology Economy

- CNBC – Technology  
  https://www.cnbc.com/id/19854910/device/rss/rss.html

- Reuters – Technology  
  https://www.reutersagency.com/feed/?best-topics=technology&post_type=best

- Bloomberg – Technology  
  https://feeds.bloomberg.com/technology/news.rss

## Infrastructure & Semiconductor Focus

- Semiconductor Engineering  
  https://semiengineering.com/feed/

---

# 4. System Architecture (LangGraph MAS)

## 4.1 High-Level Pipeline

RSS → Ranking → Extraction → Script → Validation → Image Generation → TTS → Render → Output

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

---

# 6. Agent Definitions

## Agent 1 — RSS Collector

Responsibilities:

- Fetch RSS feeds
- Normalize entries
- Deduplicate by URL and title hash
- Store candidates in database

---

## Agent 2 — Relevance Ranker

Responsibilities:

- Use OpenAI embeddings
- Compute semantic similarity to AI & Tech
- Filter top candidate
- Penalize generic clickbait

---

## Agent 3 — Article Extractor

Responsibilities:

- Scrape article HTML
- Remove ads and navigation text
- Normalize into structured format
- Extract clean paragraphs

---

## Agent 4 — Script Writer (OpenAI LLM)

Responsibilities:

- Generate strong hook
- Create 6–10 scenes
- Limit to 3 image prompts
- Optimize pacing for 35–60 seconds
- Include source line
- Output must strictly follow JSON schema

No free-form text allowed.

---

## Agent 5 — Script Validator

Checks:

- Valid JSON
- Duration between 35–60 seconds
- Maximum 3 images
- Scene count between 6–10
- Text length constraints

If validation fails:
Return structured correction instructions.

---

## Agent 6 — Image Generator (OpenAI)

Policy:

- Always generate images
- Maximum 3 unique prompts
- Vertical framing (9:16)
- No embedded text in images

---

## Agent 7 — TTS Generator (OpenAI)

Responsibilities:

- Convert narration into speech
- Generate single combined audio file
- Measure real duration
- Adjust timing if necessary

---

## Agent 8 — Video Renderer (MoviePy)

Specifications:

- Resolution: 1080x1920
- Vertical format (9:16)
- Deterministic template (v1)
- Fade transitions
- Subtitle overlay
- Static layout system

Renderer must not invent content.

---

## Agent 9 — Reporter

Responsibilities:

- Log all pipeline stages
- Save metadata
- Track execution time
- Store model versions
- Track approximate cost

---

# 7. OpenAI Model Usage Policy

OpenAI models are used for:

- Embeddings (ranking)
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

- 35–60 seconds total
- 6–10 scenes
- Maximum 3 image prompts
- Concise narration
- Hook shorter than 15 words

---

# 9 . Structural Organization & Production Hardening

VideoGeneration/
├── IMPLEMENTATION_PLAN.md
├── CONTEXT.md
├── CHANGELOG.md
├── README.md
├── requirements.txt              # Agregador opcional (referencia requirements/*.txt)
├── .env                          # API keys (NÃO versionado)
├── .gitignore
│
├── requirements/                 # Source of truth das dependências
│   ├── base.txt                  # Runtime mínimo (Phase 0)
│   ├── dev.txt                   # Ferramentas de desenvolvimento/teste
│   ├── phase1.txt                # RSS Discovery
│   ├── phase2.txt                # Relevance Ranking (Embeddings)
│   ├── phase3.txt                # Article Extraction
│   ├── phase4.txt                # Script Generation (LLM)
│   ├── phase5.txt                # Validation Loop
│   ├── phase6.txt                # Image Generation
│   ├── phase7.txt                # TTS & Audio
│   ├── phase8.txt                # Video Rendering
│   └── phase9.txt                # Production Hardening / Scheduler
│                 
│
├── configs/
│   ├── rss_feeds.yaml
│   ├── openai.yaml
│   └── pipeline.yaml
│
├── prompts/
│   ├── script_writer/
│   └── validator/
│
├── schemas/
│   ├── article_schema.json
│   └── script_schema.json
│
├── agents/
│   ├── rss_collector.py
│   ├── relevance_ranker.py
│   ├── article_extractor.py
│   ├── script_writer.py
│   ├── script_validator.py
│   ├── image_generator.py
│   ├── tts_generator.py
│   ├── video_renderer.py
│   └── reporter.py
│
├── graphs/
│   └── news_to_video_graph.py
│
├── render/
│   └── templates/
│       └── v1/                   # Template versionado
│
├── outputs/
│
└── data/
    └── db/
        └── app.sqlite

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



