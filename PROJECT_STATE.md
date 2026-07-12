# SVG-to-Nuke — Project State

**Last updated:** 2026-07-12  
**Repo:** https://github.com/cbrown323/SVG-to-Nuke  
**Status:** F-1 sync fix merged from `main` (SMIL + CSS/Lottie freeze, two-pass capture). F-5 UV coverage fix verified. Object ID pass + async progress panel on feature branch.

---

## Purpose

Import animated vector graphics into Foundry Nuke as PNG frame sequences with optional UV/ST passes for retexturing via STMap.

Supports three input formats:

| Extension | Type | Rasterization strategy |
|-----------|------|------------------------|
| `.svg` | Animated SVG (SMIL / CSS / JS) | Load in Chromium; scrub via Web Animations API (`currentTime`) |
| `.json` | Lottie / Bodymovin | Temp HTML host + vendored lottie-web; frame-accurate `goToAndStop` |
| `.html` | Custom host page | Same as SVG unless page exposes `window.lottieAnim` |

### Design decision

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
│    • File picker + parameter panel (W/H, frames, fps, UV, Object ID) │
│    • Background QProcess → external Python                           │
│    • Creates Read node(s) for color (+ UV / ID if requested)         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  External Python (Playwright + Chromium)                        │
│                                                                 │
│  svg_to_frames.py                                               │
│    • Launch headless Chromium                                   │
│    • Detect Lottie vs time-based animation                      │
│    • Screenshot each frame (optional UV / Object ID passes)     │
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
  animation.id.0001.png    # only when --id-pass
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
2. Per frame, JS injects an SVG `<pattern>` (64×64 RG gradient tile) and repaints each rendered shape (`path`, `rect`, `circle`, `ellipse`, `polygon`, `polyline`, `line`, `text`, `tspan`, `use`) with `url(#nukeUvPattern)`.
3. The repaint mirrors the element's computed style: fills are only replaced where a fill renders, strokes are only replaced where a stroke renders (`fill:none` stays `none`; stroke-only shapes keep stroke coverage). Geometry inside `clipPath`/`mask`/`pattern`/`marker`/`filter` is left untouched so alpha-defining shapes aren't altered. Zero-area shapes (straight lines) get a flat mid-UV color since `objectBoundingBox` patterns can't render on them.
4. Pattern uses `objectBoundingBox` so **R = local U, G = local V** within each shape's bounds.
5. A second screenshot writes the UV frame; original inline styles are restored before the next color frame.

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

### Root cause

For **Lottie** files, the rasterizer:

1. Scrubs only `window.lottieAnim.goToAndStop(i)` per frame.
2. Does **not** pause or scrub secondary CSS/Web Animations layers.
3. During UV capture (inject shader → screenshot → clear), real time passes and CSS layers (hearts) keep animating.
4. Color screenshot locks Lottie to frame `i`; UV screenshot fires milliseconds later with CSS layers advanced → **same frame number, different pose**.

### Fix (applied to `svg_to_frames.py`, 2026-07-12)

1. **All animation layers paused up front** (Lottie + CSS/Web Animations + SMIL via `svg.pauseAnimations()`), for every input type.
2. **Before every screenshot** (color AND every AOV pass), `sync_to_frame(i)` re-asserts:
   - Lottie frame: `goToAndStop(i, true)` (CSS/WAAPI inside the Lottie SVG is skipped to avoid ghost geometry)
   - CSS timeline: `document.getAnimations().forEach(a => { a.pause(); a.currentTime = t_ms; })`
   - SMIL: `svg.setCurrentTime(t_sec)` on each `<svg>`
3. **Two-pass capture:** all color frames, then each AOV pass — no interleaved UV delay between color frames.
4. Color and AOV captures are atomically synchronized even though DOM injection adds delay.

Additionally, the UV repaint now preserves pixel coverage for **any** input (fixes wing/trail alpha mismatch): strokes are repainted with the UV pattern instead of being zeroed, `fill:none` is respected, more element types are covered, and mask/clip geometry is skipped. Verified 0 mismatched alpha pixels (>8/255 tolerance) across all frames on a test asset with filled paths, stroke-only wings, stroke-only trail lines, and a dashed stroke.

---

## Enhancement requests

| ID | Area | Description | Status |
|----|------|-------------|--------|
| **E-7** | AOV | Object ID pass (unique RGB per fill/stroke) | **Done** |
| **E-8** | UX | Async rasterize + progress panel (Nuke stays interactive) | **Done** |
| E-2 | UX | Hide/disable frame/FPS panel fields when Lottie detected | Open |
| E-3 | UX | Expose `--selector` in Nuke panel for cropped captures | Open |
| E-4 | Repo | `requirements.txt`, sample test assets | **Done** |
| E-5 | Lottie | Offline / vendored lottie-web (no unpkg CDN) | **Done** |
| E-6 | Platform | Cross-platform `SVG_RASTER_PYTHON` defaults / docs | Open |

---

## Fix backlog (prioritized for foreman)

| Priority | ID | Area | Description | Status |
|----------|-----|------|-------------|--------|
| **P0** | F-1/F-2/F-3 | Sync | Animation sync fix — pause all layers, re-sync before each screenshot | **Done (2026-07-12)** |
| P1 | F-4 | UV | Validate sync fix resolves tail/heart misalignment on emoji sticker asset | Open — needs user re-test in Nuke |
| P1 | F-5 | UV | Stroke coverage + expanded selector (`line`, `text`, `tspan`, `use`) in UV pass | **Done (2026-07-12)** — verified pixel-exact alpha on test asset |
| P2 | E-4 | Repo | `requirements.txt`, sample assets | **Done** |
| P3 | E-2 | UX | Panel improvements for Lottie vs non-Lottie | Open |

---

## Dependencies

| Component | Dependency |
|-----------|------------|
| Nuke side | `nuke` module (ships with Nuke) |
| Raster side | `playwright`, Chromium browser binary |
| Lottie `.json` | Vendored lottie-web@5.12.2 (`vendor/lottie-web/lottie.min.js`) — offline |

---

## Known limitations (remaining after sync fix)

### Animation coverage

- Non-Lottie scrubbing uses Web Animations API only — `requestAnimationFrame`-only animations may not scrub.
- Frame count / FPS ignored for Lottie when **Auto** is enabled (panel still shows fields).

### UV pass

- Per-shape bounding-box UVs, not atlas UVs.
- Strokes carry the UV pattern of their shape's bounding box (coverage matches color pass, but stroke UVs are not arc-length parameterized).
- Zero-area shapes (straight horizontal/vertical lines) render flat mid-UV color (R=G=0.5) instead of a gradient, since `objectBoundingBox` patterns cannot paint on degenerate boxes.

### Operational

- `_debug_first_load.png` written every run.
- No automated tests in repo.

---

## Foreman workflow handoff

**Start here:** `FOREMAN_PLAN.md` — phased plan for smoke test, auto STMap graph, batch import, logging, re-render, and fidelity work.

**Completed (prior sprint):**

1. ~~Apply **F-1 sync fix** to `svg_to_frames.py`~~ — **done 2026-07-12**, merged from `main` + F-5 UV coverage fix.
2. Re-render emoji sticker asset with UV pass; confirm timing/scale match in Nuke STMap comp (F-4) — manual, user-side.
3. ~~Add README + `requirements.txt`~~ — done.

**Next implementation order (see `FOREMAN_PLAN.md`):**

| Priority | ID | Task |
|----------|-----|------|
| P0 | T-1 | Automated smoke test (F-1 / UV alpha regression) |
| P1 | T-2 | Auto STMap graph on UV import |
| P2 | T-3 | Batch import + Nuke output path override |
| — | T-4–T-8 | `render.log`, re-render Read, rAF scrubbing, Lottie/foreignObject audits |

**Artifacts:**

- `FOREMAN_PLAN.md` (implementation plan — **read first**)
- `PROJECT_STATE.md` (this file)
- `DEV_LOG.md` (development history)
- `nuke_svg_import.py`, `svg_to_frames.py` (sync fix + UV/ID AOV passes)
- `test_assets/rocket_test.svg` + `test_assets/check_alpha.py` (alpha-parity regression helper; to be wrapped by T-1 smoke test)

---

## Repository layout

```
SVG-to-Nuke/
├── nuke_svg_import.py    # Nuke menu + subprocess + Read nodes
├── svg_to_frames.py      # Playwright rasterizer CLI (F-1 sync + UV/ID AOV passes)
├── PROJECT_STATE.md      # This file
├── DEV_LOG.md            # Development log
└── README.md             # Install and usage guide
```
