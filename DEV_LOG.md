# SVG-to-Nuke — Development Log

Chronological record of project decisions, ingestion, and planned work.

---

## 2026-07-12 — Session 0: Project ingestion (Cursor Cloud Agent)

### Context

- User developed the tool in **Claude web** and shared prior chat:  
  https://claude.ai/share/58a60fd6-343a-458d-bb26-46514c5c6ca9
- User pasted full source for `nuke_svg_import.py` and `svg_to_frames.py`.
- Request: ingest codebase, review Claude chat, create **dev log** and **project state**.
- Explicit constraint: **do not alter code** in this session.

### Actions taken

1. Repo surveyed — GitHub `cbrown323/SVG-to-Nuke`, initial commit only.
2. Source ingested — both Python files added verbatim.
3. Claude share link not readable from cloud agent (SPA/auth) — fix list marked TBD.
4. Created `PROJECT_STATE.md` and `DEV_LOG.md`.
5. Committed and pushed to `cursor/project-ingest-docs-efd2`; PR #1 opened.

---

## 2026-07-12 — Session 1: Claude chat ingestion (Cursor Cloud Agent)

### Context

- User pasted full **Claude web conversation** (origin story through UV pass bug report).
- Request: ingest before switching to foreman workflow.
- Constraint: **do not alter code** — documentation only.

### Claude session summary (origin → current state)

#### 1. Initial question

User asked whether a Nuke SVG reader node is possible for animated SVG graphics.

Claude outlined two approaches:

| Option | Description | Verdict |
|--------|-------------|---------|
| **1. NDK C++ Reader** | Subclass `DD::Image::Reader`, rasterize on demand | High effort; JS/CSS animation needs browser engine |
| **2. Pre-rasterize** | Python CLI → PNG sequence → normal Read node | **Recommended** for Lottie/JS-driven assets |

User confirmed: **JS-driven (Lottie-style)** sources, wants **simple Python pre-rasterize workflow**.

#### 2. Initial implementation

Claude created:

- `svg_to_frames.py` — Playwright/Chromium rasterizer (.svg / .html / .json)
- `nuke_svg_import.py` — Nuke File menu command, subprocess, auto Read node

Key behaviors documented at creation:

- Lottie JSON: frame-accurate via `lottie-web` (`totalFrames` / `frameRate`)
- Other: time-based scrub via Web Animations API
- Transparent PNG output (`omit_background=True`)
- Resolution fixed at export time (`--width` / `--height`)
- Optional `--selector` for cropping to a container (not exposed in Nuke panel)

#### 3. Installation issues (user environment)

**Issue A — `IndentationError` in `menu.py`**

User copied indented lines from docstring:

```python
import nuke_svg_import
       nuke_svg_import.install()   # ← leading spaces = error
```

Fix: both lines at column 0.

**Issue B — `SyntaxError: unicodeescape`**

User hardcoded path with backslashes inside a string passed to `os.path.join()`:

```python
os.path.join(os.path.dirname(__file__), "C:\Users\CBWorkflow\.nuke\svg_to_frames.py")
```

Fix: use default sibling-path join or `SVG_RASTER_SCRIPT` env var; never raw `\U` in strings.

**User's working `menu.py` pattern:**

```python
import batch_reframe_reads
nuke.menu("Nuke").addCommand(
    "Custom/Batch Reframe Selected Reads",
    batch_reframe_reads.run,
    "ctrl+Home"
)
import nuke_svg_import
nuke_svg_import.install()
```

#### 4. UV pass feature (Claude session)

User wanted UV/ST pass for STMap retexturing. Claude added:

- `--uv-pass` / `--uv-out` CLI flags in `svg_to_frames.py`
- UV gradient pattern injection JS (`APPLY_UV_JS` / `CLEAR_UV_JS`)
- Nuke panel checkbox + second Read node labeled "UV Pass"
- Output naming: `{base}.uv.{frame}.png`

Claude explained limitations: per-shape local UV tiles, not unified atlas; morphing paths may swim.

#### 5. User testing — confirmed bugs

Test asset: emoji sticker with floating hearts (screenshots shared in Claude chat).

| Observation | Detail |
|-------------|--------|
| Timing mismatch | UV pass frame ≠ color pass frame |
| Alpha/size mismatch | Tail different size in color alpha vs UV alpha |
| Comp failure | STMap shows UV and color out of sync on timing and scale |

User also asked: **"Would it be possible to also output a normal pass?"**

#### 6. Root cause diagnosis (Claude)

Emoji sticker uses **hybrid animation**:

- Main character: Lottie (`window.lottieAnim`)
- Floating hearts: separate **CSS/Web Animations** layers

Bug in baseline rasterizer:

- Lottie path: only calls `goToAndStop(i)` — does **not** pause/scrub CSS layers
- CSS layers keep running in real time during UV injection delay
- Color capture = Lottie@frame i + CSS@drifted time
- UV capture = Lottie@frame i + CSS@more-drifted time → **desync**

Non-Lottie path correctly pauses CSS animations upfront (line 173) — bug is **Lottie-specific + UV second-capture**.

#### 7. Fix applied in Claude session (NOT in repo)

Claude edited `svg_to_frames.py` (+80 / −56 lines):

1. **Always pause all animation layers** at start (Lottie + CSS), regardless of input type.
2. **Before every screenshot** (color and UV):
   - Re-assert Lottie: `goToAndStop(i, true)`
   - Re-assert CSS: set `currentTime` on all `document.getAnimations()`
3. Prevents drift between color and UV captures within the same frame index.

**Important:** The repo baseline ingested in Session 0 does **not** contain this fix. Foreman must apply it.

#### 8. Normal pass (requested, not implemented)

User asked for normal pass output. Claude acknowledged but focused on sync fix first. No design or code committed.

---

### Actions taken (Session 1)

1. Updated `PROJECT_STATE.md` with full Claude context, confirmed bugs, prioritized backlog.
2. Updated `DEV_LOG.md` with this entry.
3. Verified repo `svg_to_frames.py` still has pre-fix loop (sync once per iteration, no Lottie CSS pause).
4. No Python code changes.

### Repo vs Claude session delta

| Item | Claude session | Repo (`main` / PR #1) |
|------|----------------|----------------------|
| UV pass | Yes | Yes |
| Animation sync fix | Yes (+80/−56) | **No** |
| Normal pass | Requested only | No |

### Open questions resolved / remaining

| Question | Answer |
|----------|--------|
| Exact fix list from Claude? | **Yes** — sync fix (F-1), validate on emoji asset (F-4), normal pass (E-1) |
| Target OS? | Windows, Nuke 14.0v5 |
| UV failures on real assets? | **Yes** — hybrid Lottie+CSS desync confirmed |

---

## 2026-07-12 — Session 2: F-1 sync fix (Foreman / Cursor Cloud Agent)

### Context

- Foreman workflow started per user handoff.
- Primary task: apply **F-1** animation sync fix from Claude session to `svg_to_frames.py`.
- Reference docs: `PROJECT_STATE.md`, `DEV_LOG.md`, Claude share (not machine-readable).

### Changes

1. **`svg_to_frames.py`**
   - Pause Lottie (`window.lottieAnim.pause()`) and all CSS/Web Animations at rasterize start.
   - Added `sync_to_frame()` helper called before **every** color and UV screenshot.
   - For Lottie inputs: re-assert `goToAndStop(i, true)` plus CSS `currentTime` scrub each capture.
   - For non-Lottie inputs: CSS `currentTime` scrub only (unchanged behavior, now also before UV).
   - Removed Lottie-only code path that skipped CSS pause/scrub.

2. **`PROJECT_STATE.md`** — updated status, backlog, handoff checklist.

### Testing

- [ ] Re-render emoji sticker with `--uv-pass` on Windows/Nuke 14.0v5
- [ ] Confirm F-1/F-2/F-3 resolved in STMap comp
- [ ] CLI smoke test if hybrid test asset available

### Notes / blockers

- Claude share link not fetchable from cloud agent — fix implemented from documented spec in Session 1.
- No test Lottie asset in repo yet; user validation required on emoji sticker.

---

## 2026-07-12 — Session 3: F-1 re-diagnosis after Nuke mismatch (Foreman)

### Context

- User reported F-1 still broken on **Girl cycling in autumn** (72 frames).
- Color vs UV at frame 1 show different bike positions; time-offset cannot find a matching pose.

### Findings

1. 72 frames = default panel `duration=3` × `fps=24` → asset almost certainly on the **time-based SVG/HTML path**, not Lottie `totalFrames`.
2. Chromium experiment: SMIL `<animateTransform>` does **not** appear in `document.getAnimations()`; only CSS/WAAPI does. SMIL requires `svg.pauseAnimations()` + `svg.setCurrentTime(seconds)`.
3. With SMIL free-running, color and UV sample different wall-clock phases → poses in the UV sequence never appear in the color sequence (explains failed time-slip).

### Changes

- Pause/scrub SMIL clocks in addition to Lottie + CSS.
- Two-pass capture (all color, then all UV).
- Re-sync after UV apply; rAF settle before screenshot.
- Lottie host uses inline `animationData` + `DOMLoaded` wait.

### Testing

- Synthetic SMIL+CSS hybrid: old interleaved/CSS-only path showed rider delta 7–9px during UV delay; new path delta 0.
- Full CLI on hybrid HTML, 24 frames with `--uv-pass`: color/UV content centroid **max |dx|=0.00px**.

### Notes

- User must replace `~/.nuke/svg_to_frames.py` with this revision and re-import/re-render; old PNGs will still mismatch.

---

---

## 2026-07-12 — Session 4: Frame count panel + Auto detection (Foreman)

### Context

- User confirmed UV pass alignment is fixed — do not change sync/UV capture code.
- Request: replace duration-in-seconds with frame count; add **Auto** checkbox that disables manual frame entry and detects loop length from the asset.

### Changes

1. **`nuke_svg_import.py`**
   - Qt dialog: **Frames** field + **Auto** checkbox (default on).
   - Auto checked → Frames input disabled.
   - Passes `--auto-frames` or `--frames N` to rasterizer.

2. **`svg_to_frames.py`** (frame logic only — UV/sync untouched)
   - Replaced `--duration` with `--frames` / `--auto-frames`.
   - Auto: Lottie `totalFrames`; SVG/SMIL/CSS probes one loop cycle via `DETECT_LOOP_DURATION_JS`.
   - Manual: user-supplied frame count.

### Testing

- SMIL+CSS hybrid with `--auto-frames`: detected 3.000s → 72 frames @ 24fps.
- Manual `--frames 48`: wrote 48 frames.

---

## Template for future entries

```markdown
## YYYY-MM-DD — Session N: <title>

### Changes
- ...

### Testing
- ...

### Notes / blockers
- ...
```
