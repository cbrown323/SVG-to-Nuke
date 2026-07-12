"""
nuke_svg_import.py

Adds "Import Animated SVG..." to Nuke's File menu. Picks a source file
(.svg / .json Lottie / .html), shells out to svg_to_frames.py to rasterize
it to a PNG sequence, then creates a Read node pointing at the result.

Rasterization runs in a background QProcess so Nuke stays interactive; a
modeless progress panel shows per-pass frame counts while files are written.

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

import nuke

try:
    from PySide2 import QtCore, QtWidgets
except ImportError:
    from PySide6 import QtCore, QtWidgets

EXTERNAL_PYTHON = os.environ.get(
    "SVG_RASTER_PYTHON",
    r"C:\Users\CBWorkflow\AppData\Local\Microsoft\WindowsApps\python.exe",
)
RASTER_SCRIPT = os.environ.get(
    "SVG_RASTER_SCRIPT",
    os.path.join(os.path.dirname(__file__), "svg_to_frames.py"),
)

# Keep active progress dialogs alive until the job finishes.
_ACTIVE_JOBS = []


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
        self.normal_pass_cb = QtWidgets.QCheckBox(
            "Normal Pass (flat Z+ per shape, for relighting)"
        )

        layout.addRow("Width", self.width_edit)
        layout.addRow("Height", self.height_edit)
        layout.addRow("Frames", frames_row)
        layout.addRow("FPS", self.fps_edit)
        if lottie_meta:
            layout.addRow("", self.meta_label)
        layout.addRow("", self.uv_pass_cb)
        layout.addRow("", self.normal_pass_cb)

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
            "normal_pass": self.normal_pass_cb.isChecked(),
        }


class _RasterizeProgressDialog(QtWidgets.QDialog):
    """Modeless panel that runs svg_to_frames.py without blocking Nuke."""

    _PASS_LABELS = {
        "color": "Color",
        "uv": "UV",
        "normal": "Normal",
    }

    def __init__(self, cmd, job):
        parent = nuke.mainWindow() if hasattr(nuke, "mainWindow") else None
        super().__init__(parent)
        self._job = job
        self._passes = []
        self._total_frames = 0
        self._finished = False

        self.setWindowTitle("Rasterizing SVG")
        self.setMinimumWidth(480)
        self.setWindowModality(QtCore.Qt.NonModal)

        layout = QtWidgets.QVBoxLayout(self)

        self.status_label = QtWidgets.QLabel("Starting…")
        self.pass_label = QtWidgets.QLabel("")
        self.file_label = QtWidgets.QLabel("")
        self.file_label.setStyleSheet("color: #aaa;")
        self.file_label.setWordWrap(True)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)

        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(self._cancel)

        layout.addWidget(self.status_label)
        layout.addWidget(self.pass_label)
        layout.addWidget(self.progress)
        layout.addWidget(self.file_label)
        layout.addWidget(self.log)
        layout.addWidget(cancel_btn)

        self._process = QtCore.QProcess(self)
        self._process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)

        nuke.tprint("Running: " + " ".join(cmd))
        self._process.start(cmd[0], cmd[1:])

    def _append_log(self, text):
        self.log.appendPlainText(text.rstrip())
        scrollbar = self.log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _pass_index(self, pass_name):
        try:
            return self._passes.index(pass_name)
        except ValueError:
            return 0

    def _overall_progress(self, pass_name, frame, total):
        if not self._passes or total <= 0:
            return 0
        pass_idx = self._pass_index(pass_name)
        total_steps = len(self._passes) * self._total_frames
        if total_steps <= 0:
            return 0
        completed = pass_idx * self._total_frames + frame
        return int(round(100.0 * completed / total_steps))

    def _update_progress(self, pass_name, frame, total):
        label = self._PASS_LABELS.get(pass_name, pass_name.title())
        self.pass_label.setText(f"{label} pass: frame {frame} / {total}")
        self.progress.setValue(self._overall_progress(pass_name, frame, total))

    def _on_stdout(self):
        data = self._process.readAllStandardOutput().data()
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = str(data)
        for line in text.splitlines():
            if not line:
                continue
            if line.startswith("NUKE_META\t"):
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    key, value = parts[1], parts[2]
                    if key == "total_frames":
                        self._total_frames = int(value)
                        self.status_label.setText(
                            f"Rendering {self._total_frames} frames…"
                        )
                    elif key == "passes":
                        self._passes = [p.strip() for p in value.split(",") if p.strip()]
                    elif key == "last_file":
                        self.file_label.setText(os.path.basename(value))
                continue
            if line.startswith("NUKE_PROGRESS\t"):
                parts = line.split("\t")
                if len(parts) == 4:
                    _, pass_name, frame_s, total_s = parts
                    self._update_progress(pass_name, int(frame_s), int(total_s))
                continue
            nuke.tprint(line)
            self._append_log(line)

    def _on_error(self, error):
        if self._finished:
            return
        if error == QtCore.QProcess.ProcessError.Crashed:
            return  # finished() handles exit code
        nuke.message(
            f"Could not start the rasterizer:\n{self._process.errorString()}\n\n"
            f"Interpreter: {EXTERNAL_PYTHON}\n"
            "Set SVG_RASTER_PYTHON to a Python with Playwright installed."
        )
        self._cleanup()

    def _cancel(self):
        if self._process.state() != QtCore.QProcess.NotRunning:
            self._process.kill()
        self.status_label.setText("Cancelled.")
        self._cleanup()

    def _cleanup(self):
        self._finished = True
        try:
            _ACTIVE_JOBS.remove(self)
        except ValueError:
            pass
        self.close()
        self.deleteLater()

    def _on_finished(self, exit_code, _exit_status):
        if self._finished:
            return
        self._finished = True

        if exit_code != 0:
            tail = self.log.toPlainText().strip().splitlines()
            detail = "\n".join(tail[-12:]) if tail else "(no output)"
            nuke.message(f"SVG rasterization failed (exit {exit_code}):\n{detail}")
            self._cleanup()
            return

        job = self._job
        out_dir = job["out_dir"]
        base = job["base"]

        if not os.path.isdir(out_dir):
            nuke.message("No output folder was produced — check the script output above.")
            self._cleanup()
            return

        frames = _find_frames(out_dir, base)
        if not frames:
            nuke.message("No frames were produced — check the script output above.")
            self._cleanup()
            return

        first, last = frames[0], frames[-1]
        color_read = _make_read_node(job["out_pattern"], first, last)
        nuke.tprint(f"Created Read node for {len(frames)} frames ({first}-{last}).")

        x_offset = 110
        if job["want_uv"]:
            uv_frames = _find_frames(out_dir, f"{base}.uv")
            if not uv_frames:
                nuke.message("UV pass was requested but no UV frames were found.")
                self._cleanup()
                return
            uv_first, uv_last = uv_frames[0], uv_frames[-1]
            uv_read = _make_read_node(
                job["uv_pattern"], uv_first, uv_last, label="UV Pass"
            )
            uv_read["xpos"].setValue(color_read["xpos"].value() + x_offset)
            uv_read["ypos"].setValue(color_read["ypos"].value())
            x_offset += 110
            nuke.tprint(
                f"Created UV pass Read node for {len(uv_frames)} frames "
                f"({uv_first}-{uv_last})."
            )

        if job["want_normal"]:
            normal_frames = _find_frames(out_dir, f"{base}.normal")
            if not normal_frames:
                nuke.message("Normal pass was requested but no normal frames were found.")
                self._cleanup()
                return
            n_first, n_last = normal_frames[0], normal_frames[-1]
            normal_read = _make_read_node(
                job["normal_pattern"], n_first, n_last, label="Normal Pass"
            )
            normal_read["xpos"].setValue(color_read["xpos"].value() + x_offset)
            normal_read["ypos"].setValue(color_read["ypos"].value())
            nuke.tprint(
                f"Created Normal pass Read node for {len(normal_frames)} frames "
                f"({n_first}-{n_last})."
            )

        self.status_label.setText("Done.")
        self.progress.setValue(100)
        self._cleanup()


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
    want_normal = opts["normal_pass"]

    if auto_frames and lottie_meta:
        fps = str(lottie_meta["frameRate"])

    uv_pattern = out_pattern.replace("####", "uv.####") if want_uv else None
    normal_pattern = out_pattern.replace("####", "normal.####") if want_normal else None

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
    if want_normal:
        cmd.append("--normal-pass")

    job = {
        "out_dir": out_dir,
        "base": base,
        "out_pattern": out_pattern,
        "uv_pattern": uv_pattern,
        "normal_pattern": normal_pattern,
        "want_uv": want_uv,
        "want_normal": want_normal,
    }

    dialog = _RasterizeProgressDialog(cmd, job)
    _ACTIVE_JOBS.append(dialog)
    dialog.show()


def install():
    nuke.menu("Nuke").findItem("File").addCommand(
        "Import Animated SVG...", import_animated_svg
    )
