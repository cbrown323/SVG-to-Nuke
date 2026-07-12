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
- Next step (user): new chat with **foreman workflow** for fixes and enhancements.

### Actions taken

1. **Repo surveyed** — GitHub `cbrown323/SVG-to-Nuke`, initial commit only (`README.md` with title).
2. **Source ingested** — Both Python files added to repo verbatim from user paste.
3. **Claude share link** — Attempted fetch via HTTP and search. Share page is JS/auth-gated; **conversation content not recoverable** from this environment. Fix list from that session is marked **TBD** in `PROJECT_STATE.md` until user pastes it or reviews backlog.
4. **Documentation created** — `PROJECT_STATE.md` (architecture, install, UV design, limitations, backlog) and this `DEV_LOG.md`.

### Code understanding (no modifications)

#### `nuke_svg_import.py`

- Registers **File → Import Animated SVG...**
- Panel: width, height, duration, fps, UV pass checkbox.
- Shells out to external Python running `svg_to_frames.py`.
- Output directory: `{source_dir}/{basename}_frames/`.
- Creates one **Read** node for color; optional second Read labeled **UV Pass**, offset +110 in x.
- Frame discovery via regex on `{base}.{dddd}.png` and `{base}.uv.{dddd}.png`.

#### `svg_to_frames.py`

- Playwright + Chromium headless browser.
- **`.json`**: generates temp HTML, loads lottie-web from unpkg CDN, scrubs with `goToAndStop`.
- **`.svg` / `.html`**: direct `file://` load; non-Lottie uses Web Animations API time scrub.
- **UV pass**: injects SVG pattern fill per shape, screenshots, clears — per frame.
- **Clip box** computed once up front (avoids Playwright hang on continuously moving locators).
- Writes `_debug_first_load.png` on every run.

#### Design strengths noted

- Clean split: Nuke UI vs external rasterizer (avoids Playwright in Nuke).
- Lottie path uses native frame count / frame rate when available.
- UV pass is a pragmatic STMap-oriented approach without mesh unwrapping.
- Transparent PNG output (`omit_background=True`) suits comp workflows.

#### Issues / gaps flagged for foreman (static review)

- Windows-only default for `EXTERNAL_PYTHON`.
- Lottie CDN + 300 ms fixed wait — fragile offline / on slow loads.
- Non-Lottie animation coverage limited to Web Animations API.
- UV shape selector list may miss text, groups, `<use>`.
- CLI `--selector` not wired to Nuke panel.
- No `requirements.txt`, tests, or sample assets in repo.
- **Specific fixes from Claude web chat** — not imported (link inaccessible).

### Decisions

| Decision | Rationale |
|----------|-----------|
| No code changes this session | User request |
| Create `PROJECT_STATE.md` + `DEV_LOG.md` | Handoff for foreman workflow |
| Leave fix IDs as `F-?` / TBD | Claude chat content unavailable |
| Add source files unchanged | Establish repo baseline matching user's working copy |

### Open questions for next session

1. What is the **exact fix list** from the Claude web conversation?
2. Target OS(es) for production use (Windows only vs cross-platform)?
3. Should Lottie work **offline** (vendor lottie-web into repo)?
4. E2E validation environment: Nuke version, OS, sample SVG/Lottie assets?
5. UV pass: any known failures on real assets (Lottie renderer vs native SVG)?

### Next steps (foreman workflow)

- [ ] Import confirmed fix list from user / Claude chat
- [ ] Prioritize backlog in `PROJECT_STATE.md`
- [ ] Implement fixes with minimal diffs
- [ ] Add `requirements.txt` and expanded README
- [ ] CLI smoke test with sample `.svg` / `.json`
- [ ] Document `SVG_RASTER_PYTHON` setup per platform

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
