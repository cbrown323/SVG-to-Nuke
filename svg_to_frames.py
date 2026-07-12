#!/usr/bin/env python3
"""
svg_to_frames.py — Rasterize an animated SVG / Lottie JSON to a PNG frame
sequence, ready to be loaded into Nuke with a normal Read node.

Handles three input types automatically, based on file extension:

  .svg   Native SVG with inline SMIL/CSS/JS animation — loaded directly
         in Chromium, so any of those animation types "just work" the
         same way they would in a browser.
  .html  A page that already sets up the animation (e.g. a hand-built
         host page). Exposes `window.lottieAnim` if you want
         frame-accurate scrubbing; otherwise falls back to time-based
         scrubbing via the Web Animations API.
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
        --fps 24 --duration 3
"""
import argparse
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

LOTTIE_CDN = "https://unpkg.com/lottie-web@5.12.2/build/player/lottie.min.js"

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
    path: {json_path!r}
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
    const shapes = root.querySelectorAll('path, rect, circle, ellipse, polygon, polyline');
    shapes.forEach(el => {
        el.setAttribute('data-nuke-uv-applied', '1');
        el.style.fill = 'url(#nukeUvPattern)';
        el.style.stroke = 'none';
    });
}
""" % {"uv_data_uri": UV_DATA_URI}

CLEAR_UV_JS = """
() => {
    document.querySelectorAll('[data-nuke-uv-applied]').forEach(el => {
        el.style.fill = '';
        el.style.stroke = '';
        el.removeAttribute('data-nuke-uv-applied');
    });
}
"""


def make_lottie_host(json_path: Path, width: int, height: int) -> Path:
    html = LOTTIE_HOST_TEMPLATE.format(
        cdn=LOTTIE_CDN, width=width, height=height,
        json_path=f"file://{json_path.resolve()}",
    )
    tmp = Path(tempfile.mkstemp(suffix=".html")[1])
    tmp.write_text(html, encoding="utf-8")
    return tmp


def rasterize(input_path: Path, out_pattern: str, fps: float, duration: float,
              width: int, height: int, selector: str, uv_pattern: str = None):
    out_dir = Path(out_pattern).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    if uv_pattern:
        Path(uv_pattern).parent.mkdir(parents=True, exist_ok=True)

    cleanup = None
    if input_path.suffix.lower() == ".json":
        target = cleanup = make_lottie_host(input_path, width, height)
    else:
        target = input_path

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        page.on("console", lambda msg: print(f"[console:{msg.type}] {msg.text}"))
        page.on("pageerror", lambda exc: print(f"[pageerror] {exc}"))
        page.on("requestfailed", lambda req: print(f"[requestfailed] {req.url} — {req.failure}"))

        page.goto(f"file://{target.resolve()}")
        page.wait_for_timeout(300)  # let JS / lottie-web finish initializing

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
                        frameRate: window.lottieAnim.frameRate
                    };
                }
                return { isLottie: false };
            }
        """)

        if info["isLottie"]:
            total_frames = int(round(info["totalFrames"]))
            frame_rate = info["frameRate"] or fps
            print(f"Detected Lottie animation: {total_frames} frames @ {frame_rate} fps")
        else:
            total_frames = int(round(fps * duration))
            frame_rate = fps
            print(f"Using manual timing: {total_frames} frames @ {fps} fps over {duration}s")

        # Pause every animation layer up front so nothing drifts in real time
        # while we inject UV shaders or take a second screenshot.
        page.evaluate("""
            () => {
                if (window.lottieAnim) {
                    window.lottieAnim.pause();
                }
                document.getAnimations().forEach(a => a.pause());
            }
        """)

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
            """Re-assert Lottie + CSS timelines before every screenshot."""
            t_ms = (frame_index / frame_rate) * 1000
            if info["isLottie"]:
                page.evaluate(f"""
                    () => {{
                        if (window.lottieAnim) {{
                            window.lottieAnim.goToAndStop({frame_index}, true);
                        }}
                        document.getAnimations().forEach(a => a.currentTime = {t_ms});
                    }}
                """)
            else:
                page.evaluate(f"""
                    () => {{
                        document.getAnimations().forEach(a => a.currentTime = {t_ms});
                    }}
                """)

        for i in range(total_frames):
            sync_to_frame(i)

            frame_path = out_pattern.replace("####", f"{i + 1:04d}")
            if clip_box:
                page.screenshot(path=frame_path, clip=clip_box, omit_background=True)
            else:
                page.screenshot(path=frame_path, omit_background=True)

            if uv_pattern:
                sync_to_frame(i)
                uv_frame_path = uv_pattern.replace("####", f"{i + 1:04d}")
                page.evaluate(APPLY_UV_JS)
                if clip_box:
                    page.screenshot(path=uv_frame_path, clip=clip_box, omit_background=True)
                else:
                    page.screenshot(path=uv_frame_path, omit_background=True)
                page.evaluate(CLEAR_UV_JS)

        browser.close()

    if cleanup:
        cleanup.unlink(missing_ok=True)

    final_fps = info["frameRate"] if info["isLottie"] else fps
    print(f"Done — wrote {total_frames} frames to {out_dir}")
    if uv_pattern:
        print(f"Done — wrote {total_frames} UV-pass frames to {Path(uv_pattern).parent}")
    return total_frames, final_fps


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", type=Path, help="Input .svg, .html, or .json (Lottie) file")
    ap.add_argument("--out", default="frames/anim.####.png",
                     help="Output pattern using #### for the frame number")
    ap.add_argument("--fps", type=float, default=24,
                     help="Frame rate for time-based scrubbing (ignored for detected Lottie files)")
    ap.add_argument("--duration", type=float, default=3,
                     help="Duration in seconds for time-based scrubbing")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--selector", default="body",
                     help="CSS selector to screenshot (default: full body)")
    ap.add_argument("--uv-pass", action="store_true",
                     help="Also render a UV/ST pass (R=local U, G=local V per shape)")
    ap.add_argument("--uv-out", default=None,
                     help="Output pattern for the UV pass (default: derived from --out "
                          "by inserting '_uv' before the frame number)")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"Input file not found: {args.input}")

    uv_pattern = None
    if args.uv_pass:
        uv_pattern = args.uv_out or args.out.replace("####", "uv.####")

    rasterize(args.input, args.out, args.fps, args.duration,
              args.width, args.height, args.selector, uv_pattern)


if __name__ == "__main__":
    main()
