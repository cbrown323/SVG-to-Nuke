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
import json
import os
import re
import subprocess

import nuke

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide6 import QtWidgets

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


def _read_lottie_metadata(path):
    """Return {frameRate, totalFrames} from a Lottie JSON, or None."""
    if not path or os.path.splitext(path)[1].lower() != ".json":
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        fr = float(data.get("fr") or 0)
        ip = float(data.get("ip") or 0)
        op = float(data.get("op") or 0)
        if fr <= 0 or op <= ip:
            return None
        return {
            "frameRate": fr,
            "totalFrames": int(round(op - ip)),
        }
    except Exception:
        return None


class _ImportAnimatedSvgPanel(QtWidgets.QDialog):
    """Import panel with frame count + Auto toggle (Auto disables manual frames)."""

    def __init__(self, default_fps, lottie_meta=None):
        parent = nuke.mainWindow() if hasattr(nuke, "mainWindow") else None
        super().__init__(parent)
        self.setWindowTitle("Import Animated SVG")
        self.setMinimumWidth(420)
        self._lottie_meta = lottie_meta

        layout = QtWidgets.QFormLayout(self)

        self.width_edit = QtWidgets.QLineEdit("1920")
        self.height_edit = QtWidgets.QLineEdit("1080")

        native_fps = (
            lottie_meta["frameRate"] if lottie_meta else default_fps
        )
        native_frames = (
            lottie_meta["totalFrames"] if lottie_meta else 72
        )

        self.fps_edit = QtWidgets.QLineEdit(
            str(int(native_fps) if float(native_fps).is_integer() else native_fps)
        )
        self.frames_edit = QtWidgets.QLineEdit(str(int(native_frames)))

        frames_row = QtWidgets.QWidget()
        frames_layout = QtWidgets.QHBoxLayout(frames_row)
        frames_layout.setContentsMargins(0, 0, 0, 0)
        self.auto_frames_cb = QtWidgets.QCheckBox("Auto")
        self.auto_frames_cb.setChecked(True)
        self.auto_frames_cb.setToolTip(
            "Use the animation's native loop length "
            "(Lottie: frame count + fps from the JSON)."
        )
        frames_layout.addWidget(self.frames_edit)
        frames_layout.addWidget(self.auto_frames_cb)

        self.meta_label = QtWidgets.QLabel("")
        if lottie_meta:
            self.meta_label.setText(
                f"Lottie native: {lottie_meta['totalFrames']} frames @ "
                f"{lottie_meta['frameRate']} fps"
            )
            self.meta_label.setStyleSheet("color: #8af;")

        self.uv_pass_cb = QtWidgets.QCheckBox("UV Pass (for retexturing with STMap)")

        layout.addRow("Width", self.width_edit)
        layout.addRow("Height", self.height_edit)
        layout.addRow("Frames", frames_row)
        layout.addRow("FPS", self.fps_edit)
        if lottie_meta:
            layout.addRow("", self.meta_label)
        layout.addRow("", self.uv_pass_cb)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.auto_frames_cb.toggled.connect(self._on_auto_toggled)
        self._on_auto_toggled(self.auto_frames_cb.isChecked())

    def _on_auto_toggled(self, checked):
        # Auto: cannot enter a frame amount. For Lottie JSON, also lock FPS to
        # the file's native rate so Nuke project fps (24) cannot shrink 180@30
        # into 144 (= 6s * 24).
        self.frames_edit.setEnabled(not checked)
        if self._lottie_meta:
            self.fps_edit.setEnabled(not checked)
            if checked:
                fr = self._lottie_meta["frameRate"]
                self.fps_edit.setText(
                    str(int(fr) if float(fr).is_integer() else fr)
                )
                self.frames_edit.setText(str(self._lottie_meta["totalFrames"]))

    def values(self):
        return {
            "width": self.width_edit.text(),
            "height": self.height_edit.text(),
            "frames": self.frames_edit.text(),
            "fps": self.fps_edit.text(),
            "auto_frames": self.auto_frames_cb.isChecked(),
            "uv_pass": self.uv_pass_cb.isChecked(),
        }


def import_animated_svg():
    src = nuke.getFilename("Select animated SVG / Lottie JSON / HTML", "*.svg *.json *.html")
    if not src:
        return

    base = os.path.splitext(os.path.basename(src))[0]
    out_dir = os.path.join(os.path.dirname(src), f"{base}_frames")
    out_pattern = os.path.join(out_dir, f"{base}.####.png")

    lottie_meta = _read_lottie_metadata(src)
    panel = _ImportAnimatedSvgPanel(nuke.root().fps() or 24.0, lottie_meta)
    if panel.exec_() != QtWidgets.QDialog.Accepted:
        return

    opts = panel.values()
    width = opts["width"]
    height = opts["height"]
    frames = opts["frames"]
    fps = opts["fps"]
    auto_frames = opts["auto_frames"]
    want_uv = opts["uv_pass"]

    # Lottie + Auto: always pass the JSON's native fps so the rasterizer
    # never conforms duration to the Nuke root rate.
    if auto_frames and lottie_meta:
        fps = str(lottie_meta["frameRate"])

    uv_pattern = out_pattern.replace("####", "uv.####") if want_uv else None

    cmd = [
        EXTERNAL_PYTHON, RASTER_SCRIPT, src,
        "--out", out_pattern,
        "--fps", fps,
        "--width", width,
        "--height", height,
    ]
    if auto_frames:
        cmd.append("--auto-frames")
    else:
        cmd.extend(["--frames", frames])
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
