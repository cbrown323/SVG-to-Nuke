# SVG-to-Nuke — Project State

**Last updated:** 2026-07-12  
**Repo:** https://github.com/cbrown323/SVG-to-Nuke  
**Status:** Ingested, documented, no code changes yet. Fix/enhancement work deferred to foreman workflow.

---

## Purpose

Import animated vector graphics into Foundry Nuke as PNG frame sequences with optional UV/ST passes for retexturing via STMap.

Supports three input formats:

| Extension | Type | Rasterization strategy |
|-----------|------|------------------------|
| `.svg` | Animated SVG (SMIL / CSS / JS) | Load in Chromium; scrub via Web Animations API (`currentTime`) |
| `.json` | Lottie / Bodymovin | Temp HTML host + lottie-web CDN; frame-accurate `goToAndStop` |
| `.html` | Custom host page | Same as SVG unless page exposes `window.lottieAnim` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Nuke (built-in Python, no Playwright)                          │
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

## Installation (current)

1. Place both `.py` files in the same directory on `NUKE_PATH` (e.g. `~/.nuke`).
2. In `~/.nuke/menu.py`:

   ```python
   import nuke_svg_import
   nuke_svg_import.install()
   ```

3. Configure external Python with Playwright:

   ```bash
   pip install playwright
   playwright install chromium
   ```

4. Point Nuke at that interpreter via `SVG_RASTER_PYTHON` (recommended) or edit `EXTERNAL_PYTHON` in `nuke_svg_import.py`.

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SVG_RASTER_PYTHON` | Windows Store Python path (see code) | Interpreter that runs `svg_to_frames.py` |
| `SVG_RASTER_SCRIPT` | `svg_to_frames.py` beside `nuke_svg_import.py` | Override raster script path |

---

## UV pass design

When **UV Pass** is enabled:

1. Color frames render normally (transparent background).
2. Per frame, JS injects an SVG `<pattern>` (64×64 RG gradient tile) and fills each shape (`path`, `rect`, `circle`, etc.) with `url(#nukeUvPattern)`.
3. Pattern uses `objectBoundingBox` so **R = local U, G = local V** within each shape's bounds.
4. A second screenshot writes the UV frame; fills are cleared before the next color frame.

In Nuke: feed the UV Read into an **STMap** node's `uv` input for per-shape retexturing.

---

## Dependencies

| Component | Dependency |
|-----------|------------|
| Nuke side | `nuke` module (ships with Nuke) |
| Raster side | `playwright`, Chromium browser binary |
| Lottie `.json` | Network access to `unpkg.com` for lottie-web@5.12.2 |

---

## Known limitations (code review, pre-fix)

These are observations from static review — not yet validated in a full Nuke/Playwright test environment on this agent.

### Platform / environment

- **Windows-centric default** for `EXTERNAL_PYTHON`; Linux/macOS users must set `SVG_RASTER_PYTHON`.
- **Two-Python architecture** is intentional (Nuke's embedded Python lacks Playwright).

### Lottie / network

- `.json` imports require **internet** to load lottie-web from CDN.
- **Fixed 300 ms** post-load wait may be insufficient for large Lottie files or slow networks.
- No explicit wait for `lottieAnim` `DOMLoaded` / `data_ready` before reading `totalFrames`.

### Animation coverage (non-Lottie)

- Time-based scrubbing uses **Web Animations API** only (`document.getAnimations()`).
- Animations driven purely by `requestAnimationFrame`, some SMIL edge cases, or custom timers may not scrub correctly.
- Duration/FPS panel fields are **ignored for Lottie** but still shown (can confuse users).

### UV pass

- UV applies to a fixed set of SVG shape tags; **groups, text, `<use>`**, and non-standard elements may be missed.
- UV is computed **per-shape bounding box**, not per-vertex — fine for STMap retexturing of flat shapes, not for arbitrary UV unwrapping.
- Strokes are zeroed during UV capture (`stroke: none`).

### Operational / UX

- **`_debug_first_load.png`** written on every run (clutter in output folder).
- **`--selector`** exists in CLI but is **not exposed** from the Nuke panel (always full viewport / `body`).
- Subprocess errors surface **stderr only**; stdout from successful runs goes to Script Editor via `nuke.tprint`.
- No `requirements.txt` or pinned Playwright version in repo yet.
- No automated tests.

---

## Fix backlog

> **Source gap:** Prior discussion lived in [Claude web chat](https://claude.ai/share/58a60fd6-343a-458d-bb26-46514c5c6ca9). That share URL was **not readable** from the cloud agent environment (SPA/auth). Specific fix list from that session should be pasted into the next foreman chat or added here before implementation.

Placeholder priorities (to be confirmed against Claude chat + user testing):

| ID | Area | Description | Status |
|----|------|-------------|--------|
| F-? | TBD | Fixes identified in Claude web session — **needs import** | Open |
| — | Platform | Cross-platform `SVG_RASTER_PYTHON` discovery / docs | Open |
| — | Lottie | Robust load detection (event-based, not fixed timeout) | Open |
| — | Lottie | Offline / vendored lottie-web | Open |
| — | SVG | Broader animation scrubbing beyond Web Animations API | Open |
| — | UV | Cover more SVG element types; validate STMap workflow E2E | Open |
| — | UX | Hide or disable duration/FPS for Lottie in Nuke panel | Open |
| — | UX | Optional debug screenshot; expose selector in panel | Open |
| — | Repo | `requirements.txt`, README install guide, sample assets | Open |

---

## Foreman workflow (next session)

User plans a **new chat** with foreman workflow to:

1. Import confirmed fix list from Claude session (if not already in backlog).
2. Implement fixes with minimal, focused diffs.
3. Add verification steps (CLI raster smoke test; Nuke integration where available).

**Handoff artifacts for foreman:**

- This file (`PROJECT_STATE.md`)
- `DEV_LOG.md` (session history)
- Unmodified source: `nuke_svg_import.py`, `svg_to_frames.py`

---

## Repository layout

```
SVG-to-Nuke/
├── nuke_svg_import.py    # Nuke menu + subprocess + Read nodes
├── svg_to_frames.py      # Playwright rasterizer CLI
├── PROJECT_STATE.md      # This file
├── DEV_LOG.md            # Development log
└── README.md             # Repo title (minimal)
```
