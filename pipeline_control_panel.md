# AI Content Engine — Control Panel & Configuration Manual

This document maps out every single **knob, lever, configuration variable, system prompt, and database parameter** in the AI Content Engine. It outlines how to access them, how they interact, and how to manually fine-tune each stage of the pipeline to achieve peak aesthetic quality and narrative continuity.

---

## 1. End-to-End Pipeline Workflow

This diagram shows how raw inputs, configuration files, and API calls flow through the system. Each step contains active "knobs" you can adjust.

```mermaid
graph TD
    %% Config Inputs
    subgraph Config Files [Control Base]
        A["characters.yaml"]
        B[".env"]
        C["src/core/config.py"]
    end

    %% Trend Scouting
    subgraph Stage 1 [Trend Scouting]
        D["Scout Trends (LLM/FTS)"]
        D1["Knob: Niche String"]
        D2["Knob: Temperature"]
        Config Files --> D
    end

    %% Content Planning
    subgraph Stage 2 [Content Planning]
        E["Generate Daily Plan (Gemini/Groq)"]
        E1["PLANNER_SYSTEM_PROMPT"]
        E2["Knob: Post Schedule Timeframes"]
        D --> E
    end

    %% Ghostwriting
    subgraph Stage 3 [Ghostwriting]
        F["Ghostwrite Content (Groq/OpenRouter)"]
        F1["WRITER_SYSTEM_PROMPT"]
        F2["Knob: 3-Post Sliding Window"]
        E --> F
    end

    %% QA Gate
    subgraph Stage 4 [Quality Assurance]
        G["QA Gate (Groq/OpenRouter)"]
        G1["QA_SYSTEM_PROMPT"]
        G2["Knob: Pass Threshold (>=7.0)"]
        G3["Knob: Max Retries (3x)"]
        F --> G
    end

    %% Media Generation
    subgraph Stage 5 [Media Production]
        H{"Media Pathway Router"}
        G -- Pass --> H
        
        %% Pathway A: FLUX
        H -->|Static Post| I["Pollinations FLUX"]
        I1["Knob: Image Prompt structure"]
        I2["Knob: Size (1024x1024)"]
        
        %% Pathway B: Quote Card
        H -->|Quote Card| J["PIL Quote Card Renderer"]
        J1["Knob: Color/Font templates"]
        J2["Knob: Border Width / Margins"]
        
        %% Pathway C: Video Reel
        H -->|Reel Post| K["FFmpeg Reel Compositor"]
        K1["Knob: edge-tts voice code"]
        K2["Knob: Pexels Video queries"]
        K3["Knob: ASS subtitle styling"]
        K4["Knob: FFmpeg CRF/Preset"]
    end

    %% Database & Queue
    subgraph Stage 6 [Publishing Queue]
        L[("SQLite DB (content_queue)")]
        I --> L
        J --> L
        K --> L
    end
```

---

## 2. Configuration Files & System Knobs

### 2.1 The Character Panel (`config/characters.yaml`)
This is the creative heart of the system. You edit this file to tweak the characters' identities, visual aesthetics, voices, and faceless video styles.

*   **Location**: `d:\Open Projects\Content Engine\config\characters.yaml`

| Variable | Type | Allowed Values | What It Controls / How To Tune It |
| :--- | :--- | :--- | :--- |
| `status` | String | `"active"`, `"inactive"` | Controls if the scheduler generates plans for this character today. Set to `"inactive"` to pause. |
| `role` | String | Text description | Sent to the Trend Scout to find niche keywords (e.g. `"Indie game dev"`). |
| `personality` | Text | Long description | The core system behavioral guideline sent to the Writer LLM and QA scorer. |
| `visual_keywords`| String | Comma-separated | Baseline keywords appended to all FLUX prompts to anchor facial/room consistency. |
| `voice` | String | edge-tts voice code | Controls the vocal identity in the Reels pipeline (e.g. `en-US-AnaNeural` vs `en-US-GuyNeural`). |
| `themes` | YAML List | List of strings | Specific topics the planner rotates through to prevent content fatigue. |
| `reel_style` | String | Text description | Directs how the B-roll compositor overlays visuals for faceless accounts. |

---

### 2.2 The Environment Panel (`.env`)
Manages your API keys and social credentials.

*   **Location**: `d:\Open Projects\Content Engine\.env`

| Variable | Access Key | Recommended Provider / Limit Tiers |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Google AI Studio | Free tier: 1,500 requests/day. Best for planning and arc evolution. |
| `GROQ_API_KEY` | Groq Console | Free tier: 14,400 requests/day. Best for copywriting and QA scoring. |
| `OPENROUTER_API_KEY`| OpenRouter | Best for rotating through fallback free models when Groq/Gemini are down. |
| `PEXELS_API_KEY` | Pexels API | Used to query and download vertical B-roll stock footage. |
| `DEEPGRAM_API_KEY` | Deepgram Console | Used to extract precise word-level subtitle timestamps from TTS audio files. |

---

## 3. Execution Control Panels

### 3.1 LLM Router Control Panel
Governs how tasks are routed, which models are utilized, and what temperature (creativity vs. logic) is applied.

*   **File**: `src/llm/router.py`
*   **Knob Location**: `ROUTING_TABLE` map and `generate()` parameters.

```python
# Knobs to tweak model priority
ROUTING_TABLE = {
    TaskType.PLANNING: ["gemini", "groq", "openrouter"],         # Deep planning/context
    TaskType.CREATIVE_WRITING: ["groq", "gemini", "openrouter"],  # High speed, creative copywriting
    TaskType.QA_SCORING: ["groq", "openrouter", "gemini"],        # Fast logical validation
    TaskType.SIMPLE: ["groq", "gemini", "openrouter"]             # Quick extraction/conversions
}
```

*   **Model IDs (Highly Tweakable)**:
    *   **Groq Primary**: `llama-3.3-70b-versatile` (excellent human-like writing, ultra-fast).
    *   **Gemini REST**: `gemini-1.5-flash` (changed from `2.0` due to broader free tier project activations on GCP keys).
    *   **OpenRouter Free**: `meta-llama/llama-3.1-8b-instruct:free` (stable, infinite fallback).
*   **Temperature (Creativity Slider)**:
    *   `0.85` for **Creative Writing** (creates expressive, imperfect, and engaging caption styles).
    *   `0.70` for **Content Planning** (balanced logic and niche relevance).
    *   `0.30` for **QA Scoring** (cold, deterministic logical evaluation).

---

### 3.2 Content Planner & Memory Control Panel
Balances fresh trend inputs with character storyline continuity.

*   **File**: `src/generation/planner.py` and `src/memory/manager.py`

#### Knobs & Levers:
1.  **Trend Count Knob** (in `get_niche_trends`):
    *   Adjust `Identify 3 highly trending topics` -> increase to `5` or `10` to widen planning choices.
2.  **Sliding Window Memory Size Lever** (in `get_recent_posts`):
    *   Adjust `limit: int = 3`.
    *   **Tuning**: Increase to `5` to force the LLM to remember further back (minimizes topic duplication but eats context tokens).
3.  **Narrative Plot Memory Limit Lever** (in `get_recent_events`):
    *   Adjust `limit: int = 5`.
    *   **Tuning**: Represents the last 5 storyline shifts (e.g. *"soldered custom Game Boy"*, *"spilled coffee on workbench"*). Increase to inject richer history.

---

### 3.3 Quality Assurance (QA) Scoring panel
Acts as the automated editor-in-chief, filtering out robotic text or off-brand slop.

*   **File**: `src/generation/qa.py` and `src/llm/prompts.py` (QA_SYSTEM_PROMPT)

#### Knobs & Levers:
1.  **Strictness Threshold Knob** (in `assess_post`):
    *   `score_data["overall_score"] >= 7.0` (and `passed = True`).
    *   **Tuning**: Increase to `8.0` or `8.5` to force ultra-premium content, but accept a higher rejection rate. Decrease to `6.0` to speed up pipeline staging.
2.  **Max Retries Lever** (in `assess_post`):
    *   `post.retry_count >= 3`.
    *   **Tuning**: Controls how many times the writing LLM will attempt to rewrite a rejected draft based on QA feedback before flagging it as a hard failure. Set to `5` for stubborn/complex prompt alignments.

---

## 4. Visual & Media Generation Panels

### 4.1 Pillow Quote Card Styling Panel (100% Offline & Free)
Renders local, branded graphic cards inside your virtual environment.

*   **File**: `src/generation/image.py` -> `generate_quote_card`
*   **Knob Location**: `styles` dictionary.

```python
# Edit this dictionary to manually override brand colors, sizes, and styling
styles = {
    "maya_tech": {
        "bg": "#0f0f1b",          # Deep space / dark coding theme
        "text": "#00f0ff",        # Cyber cyan primary text
        "border": "#ff007f",      # Neon pink border
        "secondary": "#ffffff",   # Footer text color
        "font_size": 48           # Text size
    },
    "luna_art": {
        "bg": "#fcf8f2",          # Warm cozy cream
        "text": "#503e2c",        # Coffee espresso text
        "border": "#c9ada7",      # Dusty rose border
        "secondary": "#8a7a6b",
        "font_size": 44
    }
}
```

*   **Border Width Knob**:
    *   `draw.rectangle([40, 40, 1040, 1040], outline=style["border"], width=6)`
    *   **Tuning**: Change `width=6` to thicker neon margins (`width=12`) or thins (`width=2`).
*   **Font Selection Knobs**:
    *   `font_paths`: Configured to automatically pick Windows clean system fonts (`consola.ttf` for Maya's coding vibe, `georgia.ttf` for Luna's literary aesthetic). Add paths to custom Google Fonts (`Inter-Bold.ttf`) for premium typography!

---

### 4.2 Pollinations FLUX Character Image Panel (Free Cloud GPU)
Queries the free Pollinations AI FLUX engine to generate character portraits on demand.

*   **File**: `src/generation/image.py` -> `generate_ai_character_image`
*   **Knob Location**: The prompt string generator in `test_generation.py` or master runner.

```python
# The perfect formula for visual consistency
prompt = f"cyberpunk female game developer, {post.image_prompt or 'neon, retro computers'}"
```

*   **Size Knobs**:
    *   `?width=1024&height=1024&nologo=true`
    *   **Tuning**: Adjust width/height to vertical ratios (`720x1280` or `1080x1920`) if generating full vertical static posts.
*   **Prompt Engineering Lever**:
    *   Always append consistent details (e.g. *"translucent glasses, messy purple bun, wearing oversized hoodie"*) to the base character profile prompt to force higher facial and outfit consistency across daily generations without needing LoRAs!

---

## 5. Video Composition & Render Panels

This is our most highly engineered pipeline. It compiles professional B-roll reels entirely on CPU.

*   **Files**: `src/generation/video.py` and `src/generation/tts.py`

### 5.1 Subtitle & Caption Styles Panel (.ass Subtitles)
Controls the layout, typography, highlight colors, and positions of the word-by-word animated subtitles.

*   **File**: `src/generation/video.py` -> `generate_ass_subtitles`
*   **Style Knobs (Advanced SSA Styling)**:

```
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic...
Style: Normal,Arial,64,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0...
Style: Highlight,Arial,72,&H0000FFFF,&H000000FF,&H00000000,&H00000000,1,0...
```

*   **Primary Subtitle Size Knob**:
    *   `Fontsize,64` for standard, `72` for highlight.
    *   **Tuning**: Increase to `90` or `100` for massive, fast-paced "Hormozi" center-screen captions.
*   **Outline Thickness Knob**:
    *   `Outline,6`. Draws a thick black border around text to ensure legibility on ANY B-roll footage.
*   **Highlight Color Knob**:
    *   `PrimaryColour` in ASS format: `&HAABBGGRR` (Alpha, Blue, Green, Red in hex).
    *   `&H00FFFFFF` = Opaque White.
    *   `&H0000FFFF` = Opaque Yellow (Highlight style).
    *   **Tuning**: Change Highlight `PrimaryColour` to `&H00FF00FF` (Neon Pink) or `&H0000FF00` (Lime Green) to match character branding!

---

### 5.2 FFmpeg Vertical Composition Filter
Calculates automatic center-cropping for arbitrary stock footage sizes and applies the subtitle overlays.

*   **File**: `src/generation/video.py` -> `compose_reel`
*   **Knob Location**: `filter_complex` pipeline.

```python
"-filter_complex", f"[0:v]crop=w='min(in_w,in_h*9/16)':h='min(in_h,in_w*16/9)',scale=1080:1920,subtitles='{escaped_ass_path}':force_style='Alignment=5,MarginV=960'[v]"
```

*   **Alignment Knob (`Alignment=5`)**:
    *   `5` positions the subtitle block dead-center of the screen. Change to `2` to place it bottom-center.
*   **Margin Knob (`MarginV=960`)**:
    *   Vertical margin in pixels from the bottom/top. At `1080x1920` size, `960` offsets the subtitles perfectly center-screen vertically! Change to `200` if alignment is set to `2` to display captions near the lower third.
*   **FFmpeg Speed vs Quality Knobs**:
    *   `"-c:v", "libx264", "-preset", "ultrafast"` -> `ultrafast` preset renders CPU video in under 3 seconds. Change to `veryfast` or `medium` to compress smaller files at slightly higher quality (good for production staging).
    *   `"-crf", "26"` -> Constant Rate Factor (video quality). Lower = higher quality but larger files. Standard: `18-28`. Keep at `23` or `26` for rapid CPU render loops.

---

## 6. Pipeline Database Control Panel (SQLite Tables)

You can query, pause, reschedule, or manually inject content plans directly inside the SQLite database using any standard SQLite explorer (e.g. DB Browser for SQLite) or programmatic queries.

*   **Database Path**: `d:\Open Projects\Content Engine\data\content_engine.sqlite3`

### 6.1 State Machine Controls (`content_queue` Table)
You can manual override post staging by changing the `state` field of a row:

| State Value | What It Represents / How to Manual Override |
| :--- | :--- |
| `"planned"` | The planner has scheduled it, but text copy is not written yet. |
| `"scripted"` | Copy/Tweet/Script has been ghostwritten by the LLM, but QA hasn't run. |
| `"staged"` | **Passed QA and Media is generated successfully.** Ready for publishing. |
| `"published"` | Successfully posted on Instagram or X. Keeps history locked. |
| `"failed"` | Hard QA reject or publishing error. Review the `error_message` column. |
| `"held"` | **Manual Hold Toggle**. Set to `"held"` to keep a post in the queue indefinitely for manual tweaking before release. |

### 6.2 Manual Injection / Rescheduling
- **Reschedule**: Change the `scheduled_time` column (formatted as `YYYY-MM-DD HH:MM:SS.mmmmmm`) to push a post forward or backward in your content plan.
- **Manual Caption Tweak**: Directly edit the `caption` column if you want to rewrite an LLM-generated caption before the publisher runs. The publisher will post exactly what is in this cell!
