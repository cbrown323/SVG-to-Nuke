#!/usr/bin/env python3
"""
svg_to_frames.py — Rasterize an animated SVG / Lottie JSON to a PNG frame
sequence, ready to be loaded into Nuke with a standard Read node.

Handles three input types automatically, based on file extension:

  .svg   Native SVG with inline SMIL/CSS/JS animation — loaded directly
         in Chromium, so any of those animation types "just work" the
         same way they would in a browser.
  .html  A page that already sets up the animation (e.g. a hand-built
         host page). Exposes `window.lottieAnim` if you want
         frame-accurate scrubbing; otherwise falls back to time-based
         scrubbing via the Web Animations API + SVG SMIL clock.
  .json  Assumed to be a Lottie/Bodymovin export. A temporary host page
         is generated that loads lottie-web from a CDN and plays it,
         then frames are scrubbed by exact frame number (not time), and
         total frame count / native frame rate are read straight off
         the animation.

Requires:
    pip install playwright
    playwright install chromium

Examples:
    python svg_to_frames.py animation.json --out renders/anim.####.png
    python svg_to_frames.py animation.svg  --out renders/anim.####.png \\
        --fps 24 --auto-frames
    python svg_to_frames.py animation.svg  --out renders/anim.####.png \\
        --fps 24 --frames 72
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

LOTTIE_CDN = "https://unpkg.com/lottie-web@5.12.2/build/player/lottie.min.js"

# Embed animation JSON as animationData so we avoid file:// CORS failures and
# can wait on DOMLoaded before scrubbing.
LOTTIE_HOST_TEMPLATE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>html,body{{margin:0;background:transparent;}}
#anim{{width:{width}px;height:{height}px;}}</style>
</head><body>
<div id="anim"></div>
<script src="{cdn}"></script>
<script>
  window.lottieAnim = lottie.loadAnimation({{
    container: document.getElementById('anim'),
    renderer: 'svg',
    loop: false,
    autoplay: false,
    animationData: {animation_data}
  }});
  window.__lottieReady = new Promise((resolve) => {{
    if (window.lottieAnim.isLoaded) {{
      resolve();
    }} else {{
      window.lottieAnim.addEventListener('DOMLoaded', () => resolve());
    }}
  }});
</script>
</body></html>
"""

# A 64x64 gradient tile: R ramps 0->255 left-to-right, G ramps 0->255
# top-to-bottom. Used as a `<pattern>` fill so each shape gets a local
# 0-1 UV coordinate mapped across its own bounding box.
UV_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAcUlEQVR4nO3XwQkAIQwAwRxo"
    "/y1fET4GZQe/AZe88s3MOnjbj6+5WgFaAVoBWgFaAVoBWgFaAVoB2gMBW3/hzAMbKMAqQCtA"
    "K0ArQCtAK0ArQCtAeyCgo94qQCtAK0ArQCtAK0ArQCtAK0B7IODyo/4Hw5wC/B8uFWoAAAAA"
    "SUVORK5CYII="
)

# Freeze every timeline we know about. SMIL is NOT covered by
# document.getAnimations() — those clocks keep running unless we call
# pauseAnimations() on each <svg>.
PAUSE_ALL_JS = """
() => {
    if (window.lottieAnim) {
        window.lottieAnim.pause();
    }
    document.getAnimations({subtree: true}).forEach(a => a.pause());
    document.querySelectorAll('svg').forEach(svg => {
        try { svg.pauseAnimations(); } catch (e) {}
    });
}
"""

APPLY_UV_JS = """
() => {
    const root = document.querySelector('#anim svg') || document.querySelector('svg');
    if (!root) return;
    let defs = root.querySelector('defs');
    if (!defs) {
        defs = document.createElementNS('http://www.w3.org/2000/svg', 'defs');
        root.insertBefore(defs, root.firstChild);
    }
    if (!defs.querySelector('#nukeUvPattern')) {
        const pattern = document.createElementNS('http://www.w3.org/2000/svg', 'pattern');
        pattern.setAttribute('id', 'nukeUvPattern');
        pattern.setAttribute('patternUnits', 'objectBoundingBox');
        pattern.setAttribute('patternContentUnits', 'objectBoundingBox');
        pattern.setAttribute('width', '1');
        pattern.setAttribute('height', '1');
        const image = document.createElementNS('http://www.w3.org/2000/svg', 'image');
        image.setAttributeNS('http://www.w3.org/1999/xlink', 'href', '%(uv_data_uri)s');
        image.setAttribute('x', '0');
        image.setAttribute('y', '0');
        image.setAttribute('width', '1');
        image.setAttribute('height', '1');
        image.setAttribute('preserveAspectRatio', 'none');
        pattern.appendChild(image);
        defs.appendChild(pattern);
    }
    const shapes = root.querySelectorAll(
        'path, rect, circle, ellipse, polygon, polyline, line, text, tspan, use');
    shapes.forEach(el => {
        // Never repaint geometry that defines alpha or other paint servers
        // (masks are luminance-based, so recoloring them changes coverage).
        if (el.closest('clipPath, mask, pattern, marker, filter')) return;

        // Mirror the element's actual rendered paints: only replace a fill
        // that exists, only replace a stroke that exists. This keeps UV-pass
        // pixel coverage identical to the color pass for any input.
        const cs = window.getComputedStyle(el);
        const hasFill = cs.fill !== 'none';
        const hasStroke = cs.stroke !== 'none' && parseFloat(cs.strokeWidth) > 0;
        if (!hasFill && !hasStroke) return;

        // objectBoundingBox paints don't render on zero-area boxes (straight
        // horizontal/vertical lines), so degenerate shapes get a flat mid-UV
        // color to preserve their coverage.
        let paint = 'url(#nukeUvPattern)';
        try {
            const b = el.getBBox();
            if (!(b.width > 0) || !(b.height > 0)) paint = 'rgb(128,128,0)';
        } catch (e) {
            paint = 'rgb(128,128,0)';
        }

        el.setAttribute('data-nuke-uv-applied', '1');
        el.setAttribute('data-nuke-orig-fill', el.style.fill || '');
        el.setAttribute('data-nuke-orig-stroke', el.style.stroke || '');
        el.style.fill = hasFill ? paint : 'none';
        el.style.stroke = hasStroke ? paint : 'none';
    });
}
""" % {"uv_data_uri": UV_DATA_URI}

CLEAR_UV_JS = """
() => {
    document.querySelectorAll('[data-nuke-uv-applied]').forEach(el => {
        el.style.fill = el.getAttribute('data-nuke-orig-fill') || '';
        el.style.stroke = el.getAttribute('data-nuke-orig-stroke') || '';
        el.removeAttribute('data-nuke-orig-fill');
        el.removeAttribute('data-nuke-orig-stroke');
        el.removeAttribute('data-nuke-uv-applied');
    });
}
"""

# Probe one animation loop cycle for SVG/SMIL/CSS assets (ignores repeat/infinite). each fill and each stroke gets a unique saturated
# RGB. Strokes are only assigned an ID when the element actually renders a stroke.
APPLY_ID_JS = """
() => {
    function idToColor(id) {
        const hue = (id * 137.508) % 360;
        const s = 0.82, v = 0.96;
        const c = v * s;
        const x = c * (1 - Math.abs((hue / 60) % 2 - 1));
        const m = v - c;
        let r = 0, g = 0, b = 0;
        if (hue < 60) { r = c; g = x; }
        else if (hue < 120) { r = x; g = c; }
        else if (hue < 180) { g = c; b = x; }
        else if (hue < 240) { g = x; b = c; }
        else if (hue < 300) { r = x; b = c; }
        else { r = c; b = x; }
        return `rgb(${Math.round((r + m) * 255)},${Math.round((g + m) * 255)},${Math.round((b + m) * 255)})`;
    }

    const root = document.querySelector('#anim svg') || document.querySelector('svg');
    if (!root) return;
    let nextId = 1;
    const shapes = root.querySelectorAll(
        'path, rect, circle, ellipse, polygon, polyline, line, text, tspan, use');
    shapes.forEach(el => {
        if (el.closest('clipPath, mask, pattern, marker, filter')) return;
        const cs = window.getComputedStyle(el);
        const hasFill = cs.fill !== 'none';
        const hasStroke = cs.stroke !== 'none' && parseFloat(cs.strokeWidth) > 0;
        if (!hasFill && !hasStroke) return;

        let fillPaint = 'none';
        let strokePaint = 'none';
        if (hasFill) fillPaint = idToColor(nextId++);
        if (hasStroke) strokePaint = idToColor(nextId++);

        el.setAttribute('data-nuke-id-applied', '1');
        el.setAttribute('data-nuke-orig-fill', el.style.fill || '');
        el.setAttribute('data-nuke-orig-stroke', el.style.stroke || '');
        el.style.fill = fillPaint;
        el.style.stroke = strokePaint;
    });
}
"""

CLEAR_ID_JS = """
() => {
    document.querySelectorAll('[data-nuke-id-applied]').forEach(el => {
        el.style.fill = el.getAttribute('data-nuke-orig-fill') || '';
        el.style.stroke = el.getAttribute('data-nuke-orig-stroke') || '';
        el.removeAttribute('data-nuke-orig-fill');
        el.removeAttribute('data-nuke-orig-stroke');
        el.removeAttribute('data-nuke-id-applied');
    });
}
"""

# Probe one animation loop cycle for SVG/SMIL/CSS assets (ignores repeat/infinite).
DETECT_LOOP_DURATION_JS = """
() => {
    function parseSmilTime(value) {
        if (!value || value === 'indefinite' || value === 'media') return null;
        const v = String(value).trim();
        if (v.endsWith('ms')) return parseFloat(v) / 1000;
        if (v.endsWith('min')) return parseFloat(v) * 60;
        if (v.endsWith('h')) return parseFloat(v) * 3600;
        if (v.endsWith('s')) return parseFloat(v);
        const n = parseFloat(v);
        return Number.isFinite(n) ? n : null;
    }

    let maxSec = 0;

    document.getAnimations({ subtree: true }).forEach(a => {
        if (!a.effect || !a.effect.getComputedTiming) return;
        const t = a.effect.getComputedTiming();
        if (!Number.isFinite(t.duration) || t.duration === Infinity) return;
        const end = ((t.delay || 0) + t.duration + (t.endDelay || 0)) / 1000;
        if (end > maxSec) maxSec = end;
    });

    document.querySelectorAll('animate, animateTransform, animateMotion, set').forEach(el => {
        const begin = parseSmilTime(el.getAttribute('begin')) || 0;
        const dur = parseSmilTime(el.getAttribute('dur'));
        if (dur != null) maxSec = Math.max(maxSec, begin + dur);
        const end = parseSmilTime(el.getAttribute('end'));
        if (end != null) maxSec = Math.max(maxSec, end);
    });

    return { cycleSeconds: maxSec };
}
"""


def sanitize_lottie_parent_outpoints(animation_data: dict) -> dict:
    """Clamp layers that outlive their parent.

    lottie-web keeps parented children painting after the parent layer's out-point,
    often as degenerate sub-pixel geometry (shoe-colored 'ghost' lines through the
    character). LottieFiles site playback does not show that debris. Matching the
    child's ``op`` to the parent's removes it without touching UV sync.
    """
    def clamp_layers(layers):
        by_ind = {
            layer["ind"]: layer
            for layer in layers
            if isinstance(layer, dict) and "ind" in layer
        }
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            parent_id = layer.get("parent")
            if parent_id is None:
                continue
            parent = by_ind.get(parent_id)
            if not parent:
                continue
            parent_op = parent.get("op")
            layer_op = layer.get("op")
            if parent_op is None or layer_op is None:
                continue
            if layer_op > parent_op:
                layer["op"] = parent_op

    if isinstance(animation_data.get("layers"), list):
        clamp_layers(animation_data["layers"])
    for asset in animation_data.get("assets") or []:
        if isinstance(asset, dict) and isinstance(asset.get("layers"), list):
            clamp_layers(asset["layers"])
    return animation_data


def make_lottie_host(json_path: Path, width: int, height: int) -> Path:
    animation_data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(animation_data, dict):
        sanitize_lottie_parent_outpoints(animation_data)
    html = LOTTIE_HOST_TEMPLATE.format(
        cdn=LOTTIE_CDN, width=width, height=height,
        animation_data=json.dumps(animation_data),
    )
    tmp = Path(tempfile.mkstemp(suffix=".html")[1])
    tmp.write_text(html, encoding="utf-8")
    return tmp


def _safe_unlink(path: Path, retries: int = 8, delay: float = 0.25) -> None:
    """Delete a temp file; retry on Windows where Chromium may still hold locks."""
    for attempt in range(retries):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                print(
                    f"Warning: could not delete temp file {path} (still in use)",
                    flush=True,
                )
        except OSError as exc:
            print(f"Warning: could not delete temp file {path}: {exc}", flush=True)
            return


def read_lottie_metadata(json_path: Path):
    """Read native fr / frame count from a Lottie JSON without starting a browser."""
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "fr" not in data or "op" not in data:
        return None
    fr = float(data.get("fr") or 0)
    ip = float(data.get("ip") or 0)
    op = float(data.get("op") or 0)
    if fr <= 0 or op <= ip:
        return None
    return {
        "frameRate": fr,
        "ip": ip,
        "op": op,
        "totalFrames": int(round(op - ip)),
    }


def _sync_js(is_lottie: bool, frame_index: int, frame_rate: float,
              lottie_start: int = 0) -> str:
    """Re-assert timelines for one frame.

    Lottie: frame-accurate goToAndStop only, plus CSS/WAAPI outside the Lottie
    SVG (hybrid overlays). Do NOT drive svg.setCurrentTime on Lottie's SVG —
    that time-scrubs overlapping AE layers and ghosts limbs past segment joins.

    Non-Lottie: CSS/WAAPI + SMIL clocks.
    """
    abs_frame = lottie_start + frame_index
    t_ms = (abs_frame / frame_rate) * 1000.0
    t_sec = t_ms / 1000.0
    if is_lottie:
        return f"""
() => {{
    if (window.lottieAnim) {{
        window.lottieAnim.goToAndStop({abs_frame}, true);
    }}
    document.getAnimations({{subtree: true}}).forEach(a => {{
        const target = a.effect && a.effect.target;
        if (target && target.closest && target.closest('#anim svg')) {{
            return;
        }}
        a.pause();
        a.currentTime = {t_ms};
    }});
}}
"""
    return f"""
() => {{
    document.getAnimations({{subtree: true}}).forEach(a => {{
        a.pause();
        a.currentTime = {t_ms};
    }});
    document.querySelectorAll('svg').forEach(svg => {{
        try {{
            svg.pauseAnimations();
            svg.setCurrentTime({t_sec});
        }} catch (e) {{}}
    }});
}}
"""


def _emit_meta(key: str, value: str):
    print(f"NUKE_META\t{key}\t{value}", flush=True)


def _emit_progress(pass_name: str, frame: int, total: int):
    print(f"NUKE_PROGRESS\t{pass_name}\t{frame}\t{total}", flush=True)


def rasterize(input_path: Path, out_pattern: str, fps: float, frame_count: int,
              auto_frames: bool, width: int, height: int, selector: str,
              uv_pattern: str = None, id_pattern: str = None):
    out_dir = Path(out_pattern).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    if uv_pattern:
        Path(uv_pattern).parent.mkdir(parents=True, exist_ok=True)
    if id_pattern:
        Path(id_pattern).parent.mkdir(parents=True, exist_ok=True)

    cleanup = None
    file_meta = None
    if input_path.suffix.lower() == ".json":
        file_meta = read_lottie_metadata(input_path)
        target = cleanup = make_lottie_host(input_path, width, height)
    else:
        target = input_path

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.on("console", lambda msg: print(f"[console:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} — {req.failure}"))

        try:
            page.goto(f"file://{target.resolve()}")
            page.wait_for_timeout(300)  # let JS / lottie-web finish initializing

            # If this is a Lottie host, wait until the SVG DOM is actually built.
            page.evaluate("""
                async () => {
                    if (window.__lottieReady) {
                        await window.__lottieReady;
                    }
                }
            """)

            # Debug snapshot so you can see exactly what rendered, no matter
            # what the selector logic below does.
            debug_path = str(out_dir / "_debug_first_load.png")
            page.screenshot(path=debug_path, omit_background=True)
            print(f"Debug screenshot of the loaded page: {debug_path}")

            info = page.evaluate("""
                () => {
                    if (window.lottieAnim) {
                        return {
                            isLottie: true,
                            totalFrames: window.lottieAnim.totalFrames,
                            frameRate: window.lottieAnim.frameRate,
                            ip: window.lottieAnim.firstFrame
                        };
                    }
                    return { isLottie: false };
                }
            """)

            # .json inputs always use on-disk Lottie timing when metadata is valid,
            # even if the browser player failed to report totals.
            if info["isLottie"] or file_meta:
                frame_rate = float(
                    (file_meta or {}).get("frameRate")
                    or info.get("frameRate")
                    or fps
                )
                native_frames = int(
                    (file_meta or {}).get("totalFrames")
                    or round(info.get("totalFrames") or 0)
                    or 0
                )
                if auto_frames:
                    total_frames = max(1, native_frames)
                    print(f"Auto-detected Lottie loop: {total_frames} frames @ {frame_rate} fps "
                          f"(native — not conformed to panel FPS)")
                else:
                    total_frames = max(1, int(frame_count))
                    print(f"Using {total_frames} frames @ {frame_rate} fps (Lottie native rate)")
                info["isLottie"] = True
                lottie_start = int(
                    (file_meta or {}).get("ip")
                    or info.get("ip")
                    or 0
                )
            else:
                lottie_start = 0
                frame_rate = float(fps)
                if auto_frames:
                    cycle = page.evaluate(DETECT_LOOP_DURATION_JS)
                    cycle_sec = float(cycle.get("cycleSeconds") or 0)
                    if cycle_sec > 0:
                        total_frames = max(1, int(round(cycle_sec * frame_rate)))
                        print(f"Auto-detected loop: {cycle_sec:.3f}s -> {total_frames} frames @ {frame_rate} fps")
                    else:
                        total_frames = max(1, int(frame_count))
                        print(f"Warning: could not detect loop duration — using {total_frames} frames @ {frame_rate} fps")
                else:
                    total_frames = max(1, int(frame_count))
                    print(f"Using {total_frames} frames @ {frame_rate} fps")

            # Pause every animation layer up front so nothing drifts in real time
            # while we inject UV shaders or take a second screenshot.
            page.evaluate(PAUSE_ALL_JS)

            # Compute the crop region ONCE, up front, rather than re-checking the
            # selector's stability every frame — locator().screenshot() waits for
            # the element to stop moving before it'll shoot, which never happens
            # on a continuously animating element and causes a hang/timeout.
            clip_box = None
            if selector and selector != "body":
                box = page.locator(selector).bounding_box()
                if box:
                    clip_box = {"x": box["x"], "y": box["y"],
                                "width": box["width"], "height": box["height"]}
                else:
                    print(f"Warning: selector '{selector}' not found — using full viewport instead")

            def sync_to_frame(frame_index: int):
                page.evaluate(_sync_js(info["isLottie"], frame_index, frame_rate, lottie_start))
                # Double-rAF so layout/paint settle on the scrubbed pose before capture.
                page.evaluate("""
                    () => new Promise(resolve => {
                        requestAnimationFrame(() => requestAnimationFrame(resolve));
                    })
                """)

            def grab(path: str):
                if clip_box:
                    page.screenshot(path=path, clip=clip_box, omit_background=True)
                else:
                    page.screenshot(path=path, omit_background=True)

            passes = ["color"]
            if uv_pattern:
                passes.append("uv")
            if id_pattern:
                passes.append("id")
            _emit_meta("total_frames", str(total_frames))
            _emit_meta("passes", ",".join(passes))

            # Finish every color frame first, then each AOV pass in order.
            # Interleaving passes let wall-clock SMIL/CSS clocks (and slow DOM
            # mutation) sample different poses under the same frame index.
            for i in range(total_frames):
                sync_to_frame(i)
                path = out_pattern.replace("####", f"{i + 1:04d}")
                grab(path)
                _emit_progress("color", i + 1, total_frames)
                _emit_meta("last_file", path)

            if uv_pattern:
                for i in range(total_frames):
                    sync_to_frame(i)
                    page.evaluate(APPLY_UV_JS)
                    # Re-assert pose after DOM mutation; style.fill overrides Lottie
                    # presentation attributes, so AOV fills survive goToAndStop.
                    sync_to_frame(i)
                    path = uv_pattern.replace("####", f"{i + 1:04d}")
                    grab(path)
                    page.evaluate(CLEAR_UV_JS)
                    _emit_progress("uv", i + 1, total_frames)
                    _emit_meta("last_file", path)

            if id_pattern:
                for i in range(total_frames):
                    sync_to_frame(i)
                    page.evaluate(APPLY_ID_JS)
                    sync_to_frame(i)
                    path = id_pattern.replace("####", f"{i + 1:04d}")
                    grab(path)
                    page.evaluate(CLEAR_ID_JS)
                    _emit_progress("id", i + 1, total_frames)
                    _emit_meta("last_file", path)
        finally:
            page.close()
            browser.close()

    if cleanup:
        _safe_unlink(cleanup)

    final_fps = info["frameRate"] if info["isLottie"] else fps
    print(f"Done — wrote {total_frames} frames to {out_dir}")
    if uv_pattern:
        print(f"Done — wrote {total_frames} UV-pass frames to {Path(uv_pattern).parent}")
    if id_pattern:
        print(f"Done — wrote {total_frames} ID-pass frames to {Path(id_pattern).parent}")
    return total_frames, final_fps


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="Input .svg, .html, or .json (Lottie) file")
    ap.add_argument("--out", default="frames/anim.####.png",
                     help="Output pattern using #### for the frame number")
    ap.add_argument("--fps", type=float, default=30,
                     help="Frame rate for time-based scrubbing and auto frame math")
    ap.add_argument("--frames", type=int, default=72,
                     help="Frame count when --auto-frames is not set (ignored with --auto-frames)")
    ap.add_argument("--auto-frames", action="store_true",
                     help="Detect frame count from the animation (Lottie totalFrames or one loop cycle)")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--selector", default="body",
                     help="CSS selector to screenshot (default: full body)")
    ap.add_argument("--uv-pass", action="store_true",
                     help="Also render a UV/ST pass (R=local U, G=local V per shape)")
    ap.add_argument("--uv-out", default=None,
                     help="Output pattern for the UV pass (default: derived from --out "
                          "by inserting 'uv.' before the frame number)")
    ap.add_argument("--id-pass", action="store_true",
                     help="Also render an Object ID pass (unique RGB per fill/stroke matte)")
    ap.add_argument("--id-out", default=None,
                     help="Output pattern for the ID pass (default: derived from --out "
                          "by inserting 'id.' before the frame number)")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"Input file not found: {args.input}")

    uv_pattern = None
    if args.uv_pass:
        uv_pattern = args.uv_out or args.out.replace("####", "uv.####")

    id_pattern = None
    if args.id_pass:
        id_pattern = args.id_out or args.out.replace("####", "id.####")

    rasterize(args.input, args.out, args.fps, args.frames, args.auto_frames,
              args.width, args.height, args.selector, uv_pattern, id_pattern)


if __name__ == "__main__":
    main()
