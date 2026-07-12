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

### Next steps (foreman workflow)

- [x] Import confirmed fix list from Claude chat
- [x] Prioritize backlog in `PROJECT_STATE.md`
- [ ] Apply F-1 sync fix to `svg_to_frames.py`
- [ ] Re-test emoji sticker UV + color alignment in Nuke
- [ ] Design E-1 normal pass
- [ ] Add `requirements.txt` and expanded README
- [ ] CLI smoke test with sample `.json` (hybrid Lottie+CSS if available)

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
