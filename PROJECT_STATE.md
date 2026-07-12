# SVG-to-Nuke — Project State

**Last updated:** 2026-07-12  
**Repo:** https://github.com/cbrown323/SVG-to-Nuke  
**Status:** F-1 sync fix applied. Pending validation on emoji sticker asset in Nuke.

---

## Purpose

Import animated vector graphics into Foundry Nuke as PNG frame sequences with optional UV/ST passes for retexturing via STMap.

Supports three input formats:

| Extension | Type | Rasterization strategy |
|-----------|------|------------------------|
| `.svg` | Animated SVG (SMIL / CSS / JS) | Load in Chromium; scrub via Web Animations API (`currentTime`) |
| `.json` | Lottie / Bodymovin | Temp HTML host + lottie-web CDN; frame-accurate `goToAndStop` |
| `.html` | Custom host page | Same as SVG unless page exposes `window.lottieAnim` |

### Design decision (Claude session origin)

User requirement: **JS-driven / Lottie-style** animated graphics in Nuke.

| Approach | Verdict |
|----------|---------|
| **Option 1:** Custom NDK C++ Reader | Rejected for now — high effort; JS/CSS animation needs headless browser anyway |
| **Option 2:** Python pre-rasterize + Nuke Read node | **Chosen** — Playwright/Chromium frame-by-frame export, wrapped in Nuke menu command |

Fidelity is bounded by headless Chromium. True vector resolution-independence is **not** available — output is raster at chosen `--width`/`--height`.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Nuke 14.x (built-in Python, no Playwright)                     │
│                                                                 │
│  File → Import Animated SVG...                                  │
│       │                                                         │
│       ▼                                                         │
│  nuke_svg_import.py                                             │
│    • File picker + parameter panel (W/H, duration, fps, UV)     │
│    • subprocess → external Python                               │
│    • Creates Read node(s) for color (+ UV if requested)         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  External Python (Playwright + Chromium)                        │
│                                                                 │
│  svg_to_frames.py                                               │
│    • Launch headless Chromium                                   │
│    • Detect Lottie vs time-based animation                      │
│    • Screenshot each frame (optional UV pass per frame)         │
│    • Write PNG sequence next to source file                     │
└─────────────────────────────────────────────────────────────────┘
```

### File roles

| File | Runtime | Role |
|------|---------|------|
| `nuke_svg_import.py` | Inside Nuke | Menu hook, UI panel, subprocess orchestration, Read node creation |
| `svg_to_frames.py` | External Python 3 | Browser-based rasterization CLI |

### Output layout

For source `path/to/animation.svg`:

```
path/to/animation_frames/
  animation.0001.png
  animation.0002.png
  ...
  animation.uv.0001.png   # only when --uv-pass
  animation.uv.0002.png
  _debug_first_load.png   # always written on rasterize
```

Frame numbers are **1-based, 4-digit zero-padded** (`0001`, `0002`, …).

---

## Installation

### Files

1. Place both `.py` files in the same directory on `NUKE_PATH` (e.g. `C:\Users\CBWorkflow\.nuke` or `~/.nuke`).
2. In `~/.nuke/menu.py` — **both lines at column 0, no indent:**

   ```python
   import nuke_svg_import
   nuke_svg_import.install()
   ```

   Common mistake: copying indented lines from the docstring → `IndentationError: unexpected indent`.

3. Do **not** hardcode Windows paths inside `os.path.join()` with unescaped backslashes — use `SVG_RASTER_SCRIPT` env var or rely on the default `os.path.join(os.path.dirname(__file__), "svg_to_frames.py")`. A path like `"C:\Users\..."` causes `SyntaxError: unicodeescape`.

### External Python

Nuke's bundled interpreter does not have Playwright:

```bash
pip install playwright
playwright install chromium
```

Set before launching Nuke:

```bat
set SVG_RASTER_PYTHON=C:\path\to\python.exe
```

Or edit `EXTERNAL_PYTHON` in `nuke_svg_import.py`.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SVG_RASTER_PYTHON` | Windows Store Python path (see code) | Interpreter that runs `svg_to_frames.py` |
| `SVG_RASTER_SCRIPT` | `svg_to_frames.py` beside `nuke_svg_import.py` | Override raster script path |

### Verified environment (user)

- **OS:** Windows
- **Nuke:** 14.0v5
- **NUKE_PATH:** `C:\Users\CBWorkflow\.nuke`

---

## UV pass design

When **UV Pass** is enabled:

1. Color frames render normally (transparent background).
2. Per frame, JS injects an SVG `<pattern>` (64×64 RG gradient tile) and fills each shape (`path`, `rect`, `circle`, etc.) with `url(#nukeUvPattern)`.
3. Pattern uses `objectBoundingBox` so **R = local U, G = local V** within each shape's bounds.
4. A second screenshot writes the UV frame; fills are cleared before the next color frame.

In Nuke: feed the UV Read into an **STMap** node's `uv` input for per-shape retexturing.

### What UV pass gives you

- Per-shape local 0–1 UV tile (fabric/fur/pattern per body part).
- UVs ride along with SVG transforms (rotation, scale, translation).

### What UV pass does NOT give you

- One continuous UV atlas across the whole character.
- Topological tracking on heavy path morphing / squash-stretch (UV follows bounding box per frame → can "swim").
- A single texture wrapping continuously across all shapes (texture repeats per shape).

---

## Confirmed bugs (user testing)

Test asset: **emoji sticker** Lottie with floating hearts — main body Lottie-driven, decorative hearts are separate **CSS/Web Animations** layers.

| Symptom | User observation |
|---------|------------------|
| **F-1 Timing drift** | UV pass frame N does not match color pass frame N |
| **F-2 Scale/alpha mismatch** | Tail (and other elements) different size in color alpha vs UV alpha |
| **F-3 Comp misalignment** | STMap comp shows UV and color out of sync on timing and scale |

### Root cause (diagnosed in Claude session)

For **Lottie** files, the rasterizer:

1. Scrubs only `window.lottieAnim.goToAndStop(i)` per frame.
2. Does **not** pause or scrub secondary CSS/Web Animations layers.
3. During UV capture (inject shader → screenshot → clear), real time passes and CSS layers (hearts) keep animating.
4. Color screenshot locks Lottie to frame `i`; UV screenshot fires milliseconds later with CSS layers advanced → **same frame number, different pose**.

### Fix applied (2026-07-12 — foreman session)

1. **Pause all animation layers up front** (Lottie + CSS/Web Animations).
2. **Before every screenshot** (color AND UV):
   - Re-assert Lottie frame: `goToAndStop(i, true)`
   - Re-assert CSS timeline: `document.getAnimations().forEach(a => a.currentTime = t_ms)`
3. Ensures color and UV captures are atomically synchronized even when UV injection adds delay.

**Status:** Implemented in `svg_to_frames.py` on branch `cursor/f-1-sync-fix-52d3`. Awaiting Nuke re-test on emoji sticker asset.

---

## Enhancement requests

| ID | Area | Description | Status |
|----|------|-------------|--------|
| **E-1** | AOV | **Normal pass** output (user asked: "Would it be possible to also output a normal pass?") | Open — design TBD |
| E-2 | UX | Hide/disable duration/FPS panel fields when Lottie detected | Open |
| E-3 | UX | Expose `--selector` in Nuke panel for cropped captures | Open |
| E-4 | Repo | `requirements.txt`, README, sample test assets | Open |
| E-5 | Lottie | Offline / vendored lottie-web (no unpkg CDN) | Open |
| E-6 | Platform | Cross-platform `SVG_RASTER_PYTHON` defaults / docs | Open |

### Normal pass (E-1) — initial notes for foreman

Not implemented. Possible approaches to evaluate:

- Derive from SVG path geometry + transform stack per shape (2.5D facing-camera normals).
- Simpler fallback: flat Z+ normal (solid blue) per shape for relighting experiments.
- May share the same per-screenshot sync infrastructure as the UV pass fix.

---

## Fix backlog (prioritized for foreman)

| Priority | ID | Area | Description | Status |
|----------|-----|------|-------------|--------|
| **P0** | F-1/F-2/F-3 | Sync | Apply Claude's animation sync fix — pause all layers, re-sync before each screenshot | **Applied — pending Nuke validation** |
| P1 | F-4 | UV | Validate sync fix resolves tail/heart misalignment on emoji sticker asset | Open (after F-1) |
| P1 | F-5 | UV | Expand shape selector (`text`, `g`, `use`, etc.) if elements still missing | Open |
| P2 | E-1 | AOV | Normal pass output + Nuke Read node wiring | Open |
| P2 | E-4 | Repo | `requirements.txt`, install docs, sample assets | Open |
| P3 | E-2 | UX | Panel improvements for Lottie vs non-Lottie | Open |

---

## Dependencies

| Component | Dependency |
|-----------|------------|
| Nuke side | `nuke` module (ships with Nuke) |
| Raster side | `playwright`, Chromium browser binary |
| Lottie `.json` | Network access to `unpkg.com` for lottie-web@5.12.2 |

---

## Known limitations (remaining after sync fix)

### Animation coverage

- Non-Lottie scrubbing uses Web Animations API only — `requestAnimationFrame`-only animations may not scrub.
- Duration/FPS ignored for Lottie (panel still shows them).

### UV pass

- Per-shape bounding-box UVs, not atlas UVs.
- Strokes zeroed during UV capture.

### Operational

- `_debug_first_load.png` written every run.
- No automated tests in repo.

---

## Foreman workflow handoff

**Start here:**

1. ~~Apply **F-1 sync fix** to `svg_to_frames.py`~~ (done — see `cursor/f-1-sync-fix-52d3`)
2. Re-render emoji sticker asset with UV pass; confirm timing/scale match in Nuke STMap comp.
3. Design and implement **E-1 normal pass** if sync fix validates.
4. Add `requirements.txt` + README.

**Artifacts:**

- `PROJECT_STATE.md` (this file)
- `DEV_LOG.md` (full session history including Claude origin)
- `nuke_svg_import.py`, `svg_to_frames.py` (F-1 sync fix on feature branch)

**Claude chat reference:** https://claude.ai/share/58a60fd6-343a-458d-bb26-46514c5c6ca9

---

## Repository layout

```
SVG-to-Nuke/
├── nuke_svg_import.py    # Nuke menu + subprocess + Read nodes
├── svg_to_frames.py      # Playwright rasterizer CLI (F-1 sync fix applied)
├── PROJECT_STATE.md      # This file
├── DEV_LOG.md            # Development log
└── README.md             # Repo title (minimal)
```
