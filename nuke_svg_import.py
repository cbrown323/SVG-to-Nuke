"""
nuke_svg_import.py

Adds "Import Animated SVG..." to Nuke's File menu. Picks a source file
(.svg / .json Lottie / .html), shells out to svg_to_frames.py to rasterize
it to a PNG sequence, then creates a Read node pointing at the result.

INSTALL
-------
1. Put this file and svg_to_frames.py in the same folder, and add that
   folder to your NUKE_PATH (e.g. ~/.nuke).
2. In ~/.nuke/menu.py add:

       import nuke_svg_import
       nuke_svg_import.install()

3. Nuke's built-in Python interpreter does NOT have Playwright, so point
   EXTERNAL_PYTHON at a separate Python environment where you've run:

       pip install playwright
       playwright install chromium

   Either edit EXTERNAL_PYTHON below, or set the SVG_RASTER_PYTHON
   environment variable before launching Nuke.
"""
import os
import re
import subprocess

import nuke

EXTERNAL_PYTHON = os.environ.get(
    "SVG_RASTER_PYTHON",
    r"C:\Users\CBWorkflow\AppData\Local\Microsoft\WindowsApps\python.exe",
)
RASTER_SCRIPT = os.environ.get(
    "SVG_RASTER_SCRIPT",
    os.path.join(os.path.dirname(__file__), "svg_to_frames.py"),
)


def _find_frames(out_dir, base_name):
    frame_re = re.compile(re.escape(base_name) + r"\.(\d+)\.png$")
    frames = []
    for f in os.listdir(out_dir):
        m = frame_re.match(f)
        if m:
            frames.append(int(m.group(1)))
    frames.sort()
    return frames


def _make_read_node(pattern, first, last, label=None):
    read = nuke.createNode("Read")
    read["file"].fromUserText(f"{pattern.replace('####', '%04d')} {first}-{last}")
    read["first"].setValue(first)
    read["last"].setValue(last)
    read["origfirst"].setValue(first)
    read["origlast"].setValue(last)
    if label:
        read["label"].setValue(label)
    return read


def import_animated_svg():
    src = nuke.getFilename("Select animated SVG / Lottie JSON / HTML", "*.svg *.json *.html")
    if not src:
        return

    base = os.path.splitext(os.path.basename(src))[0]
    out_dir = os.path.join(os.path.dirname(src), f"{base}_frames")
    out_pattern = os.path.join(out_dir, f"{base}.####.png")

    panel = nuke.Panel("Import Animated SVG")
    panel.setWidth(400)
    panel.addSingleLineInput("Width", "1920")
    panel.addSingleLineInput("Height", "1080")
    panel.addSingleLineInput("Duration (s, ignored for Lottie)", "3")
    panel.addSingleLineInput("FPS (ignored for Lottie)", str(nuke.root().fps() or 24.0))
    panel.addBooleanCheckBox("UV Pass (for retexturing with STMap)", False)
    if not panel.show():
        return

    width = panel.value("Width")
    height = panel.value("Height")
    duration = panel.value("Duration (s, ignored for Lottie)")
    fps = panel.value("FPS (ignored for Lottie)")
    want_uv = panel.value("UV Pass (for retexturing with STMap)")

    uv_pattern = out_pattern.replace("####", "uv.####") if want_uv else None

    cmd = [
        EXTERNAL_PYTHON, RASTER_SCRIPT, src,
        "--out", out_pattern,
        "--fps", fps,
        "--duration", duration,
        "--width", width,
        "--height", height,
    ]
    if want_uv:
        cmd.append("--uv-pass")

    nuke.tprint("Running: " + " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        nuke.tprint(result.stdout)
    except subprocess.CalledProcessError as e:
        nuke.message(f"SVG rasterization failed:\n{e.stderr}")
        return
    except FileNotFoundError:
        nuke.message(
            f"Couldn't find the external Python interpreter:\n{EXTERNAL_PYTHON}\n\n"
            "Set the SVG_RASTER_PYTHON environment variable to a Python "
            "with Playwright installed."
        )
        return

    if not os.path.isdir(out_dir):
        nuke.message("No output folder was produced — check the script output above.")
        return

    frames = _find_frames(out_dir, base)
    if not frames:
        nuke.message("No frames were produced — check the script output above.")
        return

    first, last = frames[0], frames[-1]
    color_read = _make_read_node(out_pattern, first, last)
    nuke.tprint(f"Created Read node for {len(frames)} frames ({first}-{last}).")

    if want_uv:
        uv_frames = _find_frames(out_dir, f"{base}.uv")
        if not uv_frames:
            nuke.message("UV pass was requested but no UV frames were found — "
                          "check the script output above.")
            return
        uv_first, uv_last = uv_frames[0], uv_frames[-1]
        uv_read = _make_read_node(uv_pattern, uv_first, uv_last, label="UV Pass")
        uv_read["xpos"].setValue(color_read["xpos"].value() + 110)
        uv_read["ypos"].setValue(color_read["ypos"].value())
        nuke.tprint(f"Created UV pass Read node for {len(uv_frames)} frames "
                    f"({uv_first}-{uv_last}). Feed it into an STMap node's uv input.")


def install():
    nuke.menu("Nuke").findItem("File").addCommand(
        "Import Animated SVG...", import_animated_svg
    )
