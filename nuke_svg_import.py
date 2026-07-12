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

_BATCH_EXTENSIONS = (".svg", ".json", ".html")


def _find_frames(out_dir, base_name):
    frame_re = re.compile(re.escape(base_name) + r"\.(\d+)\.png$")
    frames = []
    for f in os.listdir(out_dir):
        m = frame_re.match(f)
        if m:
            frames.append(int(m.group(1)))
    frames.sort()
    return frames


def _resolve_output_paths(src, output_dir=None):
    """Return (base, out_dir, out_pattern) for a source file."""
    base = os.path.splitext(os.path.basename(src))[0]
    if output_dir:
        out_dir = output_dir
    else:
        out_dir = os.path.join(os.path.dirname(src), f"{base}_frames")
    out_pattern = os.path.join(out_dir, f"{base}.####.png")
    return base, out_dir, out_pattern


def _output_dir_has_existing_frames(out_dir, base):
    if not os.path.isdir(out_dir):
        return False
    return bool(_find_frames(out_dir, base))


def _discover_batch_files(folder, skip_existing=False, output_root=None):
    """Return sorted importable paths under folder."""
    folder = os.path.abspath(folder)
    found = []
    for name in sorted(os.listdir(folder)):
        if name.startswith(".") or name.startswith("_"):
            continue
        if not name.lower().endswith(_BATCH_EXTENSIONS):
            continue
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        if skip_existing:
            base = os.path.splitext(name)[0]
            _, out_dir, _ = _resolve_output_paths(path, output_root or None)
            if _output_dir_has_existing_frames(out_dir, base):
                continue
        found.append(path)
    return found


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


def _update_read_node(read, pattern, first, last):
    read["file"].fromUserText(f"{pattern.replace('####', '%04d')} {first}-{last}")
    read["first"].setValue(first)
    read["last"].setValue(last)
    read["origfirst"].setValue(first)
    read["origlast"].setValue(last)


def _build_stmap_graph(color_read, uv_read, label=None):
    """Wire Color Read → STMap ← UV Read for retexturing."""
    if uv_read is None:
        return None
    stmap = nuke.createNode("STMap")
    stmap.setInput(0, color_read)
    stmap.setInput(1, uv_read)
    stmap["xpos"].setValue(color_read["xpos"].value())
    stmap["ypos"].setValue(color_read["ypos"].value() + 80)
    if label:
        stmap["label"].setValue(label)
    nuke.tprint(f"Created STMap node wired to {color_read.name()} and {uv_read.name()}.")
    return stmap


def _find_stmap_for_color(color_read):
    for node in nuke.allNodes("STMap"):
        if node.input(0) is color_read:
            return node
    return None


def _set_invisible_knob(node, name, label, value):
    if node.knob(name):
        node[name].setValue(str(value))
        return
    knob = nuke.String_Knob(name, label, str(value))
    knob.setFlag(nuke.INVISIBLE)
    node.addKnob(knob)


def _stamp_read_metadata(color_read, src, opts, out_pattern, uv_read=None, id_read=None):
    """Store raster settings on the color Read for re-render (T-5)."""
    fields = {
        "svg_source": src,
        "svg_width": opts["width"],
        "svg_height": opts["height"],
        "svg_fps": opts["fps"],
        "svg_frames": opts["frames"],
        "svg_auto_frames": "1" if opts["auto_frames"] else "0",
        "svg_auto_fps": "1" if opts["auto_fps"] else "0",
        "svg_uv_pass": "1" if opts.get("uv_pass") else "0",
        "svg_id_pass": "1" if opts.get("id_pass") else "0",
        "svg_out_pattern": out_pattern,
        "svg_uv_read": uv_read.name() if uv_read else "",
        "svg_id_read": id_read.name() if id_read else "",
    }
    for key, value in fields.items():
        label = key.replace("svg_", "").replace("_", " ").title()
        _set_invisible_knob(color_read, key, label, value)


def _knob_text(read_node, name, default=""):
    knob = read_node.knob(name)
    return knob.value() if knob else default


def _get_svg_metadata(read_node):
    """Return stored import settings, or None if this is not an SVG import Read."""
    source = _knob_text(read_node, "svg_source")
    if not source:
        return None

    def _bool_knob(name):
        return _knob_text(read_node, name) in ("1", "true", "True")

    return {
        "source": source,
        "width": _knob_text(read_node, "svg_width"),
        "height": _knob_text(read_node, "svg_height"),
        "fps": _knob_text(read_node, "svg_fps"),
        "frames": _knob_text(read_node, "svg_frames"),
        "auto_frames": _bool_knob("svg_auto_frames"),
        "auto_fps": _bool_knob("svg_auto_fps"),
        "uv_pass": _bool_knob("svg_uv_pass"),
        "id_pass": _bool_knob("svg_id_pass"),
        "out_pattern": _knob_text(read_node, "svg_out_pattern"),
        "uv_read_name": _knob_text(read_node, "svg_uv_read"),
        "id_read_name": _knob_text(read_node, "svg_id_read"),
    }


def _find_import_siblings(color_read):
    """Locate UV/ID Reads and STMap wired to an SVG color Read."""
    meta = _get_svg_metadata(color_read)
    siblings = {"color": color_read, "uv": None, "id": None, "stmap": _find_stmap_for_color(color_read)}
    if not meta:
        return siblings

    for key, knob_name in (("uv", "uv_read_name"), ("id", "id_read_name")):
        node_name = meta.get(knob_name, "")
        if node_name:
            node = nuke.toNode(node_name)
            if node and node.Class() == "Read":
                siblings[key] = node

    if meta["out_pattern"]:
        base = os.path.basename(meta["out_pattern"]).replace(".####.png", "")
        out_dir = os.path.dirname(meta["out_pattern"])
        for node in nuke.allNodes("Read"):
            if node is color_read or node in (siblings["uv"], siblings["id"]):
                continue
            file_val = node["file"].value()
            if out_dir in file_val and base in file_val:
                if ".uv." in file_val and siblings["uv"] is None:
                    siblings["uv"] = node
                elif ".id." in file_val and siblings["id"] is None:
                    siblings["id"] = node
    return siblings


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


def _build_raster_cmd(src, out_pattern, opts, lottie_meta=None):
    fps = opts["fps"]
    if opts.get("auto_fps"):
        if lottie_meta:
            fps = str(lottie_meta["frameRate"])
        else:
            fps = str(int(_ImportAnimatedSvgPanel.DEFAULT_FPS))

    cmd = [
        EXTERNAL_PYTHON, RASTER_SCRIPT, src,
        "--out", out_pattern,
        "--fps", fps,
        "--width", opts["width"],
        "--height", opts["height"],
    ]
    if opts.get("auto_frames"):
        cmd.append("--auto-frames")
    else:
        cmd.extend(["--frames", opts["frames"]])
    if opts.get("uv_pass"):
        cmd.append("--uv-pass")
    if opts.get("id_pass"):
        cmd.append("--id-pass")
    return cmd


def _build_job_dict(src, opts, output_dir=None, **extra):
    output_dir = (output_dir or opts.get("output_dir") or "").strip() or None
    base, out_dir, out_pattern = _resolve_output_paths(src, output_dir)
    want_uv = opts.get("uv_pass", False)
    want_id = opts.get("id_pass", False)
    job = {
        "src": src,
        "opts": opts,
        "out_dir": out_dir,
        "base": base,
        "out_pattern": out_pattern,
        "uv_pattern": out_pattern.replace("####", "uv.####") if want_uv else None,
        "id_pattern": out_pattern.replace("####", "id.####") if want_id else None,
        "want_uv": want_uv,
        "want_id": want_id,
    }
    job.update(extra)
    return job


def _confirm_overwrite(base, out_dir):
    return nuke.ask(
        f"Output folder already contains frames for '{base}':\n{out_dir}\n\n"
        "Overwrite existing frames?"
    )


def _finish_import_nodes(job, frames):
    """Create or update Read nodes (+ optional STMap) after a successful rasterize."""
    first, last = frames[0], frames[-1]
    existing = job.get("existing_nodes") or {}
    rerender = bool(job.get("rerender"))
    y_offset = job.get("layout_offset_y", 0)
    x_offset = 110

    if existing.get("color"):
        color_read = existing["color"]
        _update_read_node(color_read, job["out_pattern"], first, last)
        nuke.tprint(f"Updated Read node for {len(frames)} frames ({first}-{last}).")
    else:
        color_read = _make_read_node(job["out_pattern"], first, last)
        if y_offset:
            color_read["ypos"].setValue(color_read["ypos"].value() + y_offset)
        nuke.tprint(f"Created Read node for {len(frames)} frames ({first}-{last}).")

    uv_read = existing.get("uv")
    id_read = existing.get("id")

    if job["want_uv"]:
        uv_frames = _find_frames(job["out_dir"], f"{job['base']}.uv")
        if not uv_frames:
            raise RuntimeError("UV pass was requested but no UV frames were found.")
        uv_first, uv_last = uv_frames[0], uv_frames[-1]
        if uv_read:
            _update_read_node(uv_read, job["uv_pattern"], uv_first, uv_last)
            nuke.tprint(f"Updated UV pass Read node ({uv_first}-{uv_last}).")
        else:
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
            if not rerender:
                _build_stmap_graph(color_read, uv_read, label=job["base"])
            elif not _find_stmap_for_color(color_read):
                _build_stmap_graph(color_read, uv_read, label=job["base"])

    if job["want_id"]:
        id_frames = _find_frames(job["out_dir"], f"{job['base']}.id")
        if not id_frames:
            raise RuntimeError("Object ID pass was requested but no ID frames were found.")
        id_first, id_last = id_frames[0], id_frames[-1]
        if id_read:
            _update_read_node(id_read, job["id_pattern"], id_first, id_last)
            nuke.tprint(f"Updated Object ID Read node ({id_first}-{id_last}).")
        else:
            id_read = _make_read_node(
                job["id_pattern"], id_first, id_last, label="Object ID"
            )
            id_read["xpos"].setValue(color_read["xpos"].value() + x_offset)
            id_read["ypos"].setValue(color_read["ypos"].value())
            nuke.tprint(
                f"Created Object ID Read node for {len(id_frames)} frames "
                f"({id_first}-{id_last})."
            )

    _stamp_read_metadata(
        color_read, job["src"], job["opts"], job["out_pattern"], uv_read, id_read
    )
    return color_read


def _start_rasterize_job(src, opts, output_dir=None, **job_extra):
    """Queue one rasterize job (single import, batch item, or re-render)."""
    output_dir = (output_dir or opts.get("output_dir") or "").strip() or None
    base, out_dir, _out_pattern = _resolve_output_paths(src, output_dir)

    if (
        not job_extra.get("rerender")
        and _output_dir_has_existing_frames(out_dir, base)
        and not _confirm_overwrite(base, out_dir)
    ):
        cb = job_extra.get("on_complete")
        if cb:
            cb(False, None)
        return None

    os.makedirs(out_dir, exist_ok=True)

    lottie_meta = _read_lottie_metadata(src)
    job = _build_job_dict(src, opts, output_dir, **job_extra)
    cmd = _build_raster_cmd(src, job["out_pattern"], opts, lottie_meta)

    dialog = _RasterizeProgressDialog(cmd, job)
    _ACTIVE_JOBS.append(dialog)
    dialog.show()
    return dialog


class _ImportAnimatedSvgPanel(QtWidgets.QDialog):
    """Import panel with Auto toggles for frame count and FPS."""

    DEFAULT_FPS = 30.0
    _FIELD_WIDTH = 72

    def __init__(self, default_fps, lottie_meta=None, batch_mode=False):
        parent = nuke.mainWindow() if hasattr(nuke, "mainWindow") else None
        super().__init__(parent)
        self.setWindowTitle(
            "Batch Import Animated SVG" if batch_mode else "Import Animated SVG"
        )
        self.setMinimumWidth(300)
        self.setMaximumWidth(360)
        self._lottie_meta = lottie_meta
        self._default_fps = default_fps
        self._batch_mode = batch_mode

        layout = QtWidgets.QFormLayout(self)
        layout.setFieldGrowthPolicy(QtWidgets.QFormLayout.FieldsStayAtSizeHint)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(6)

        native_frames = (
            lottie_meta["totalFrames"] if lottie_meta else 72
        )

        self.width_edit = QtWidgets.QLineEdit("1920")
        self.height_edit = QtWidgets.QLineEdit("1080")
        self.fps_edit = QtWidgets.QLineEdit(self._auto_fps_text())
        self.frames_edit = QtWidgets.QLineEdit(str(int(native_frames)))
        for edit in (self.width_edit, self.height_edit, self.fps_edit, self.frames_edit):
            edit.setFixedWidth(self._FIELD_WIDTH)

        fps_row = QtWidgets.QWidget()
        fps_layout = QtWidgets.QHBoxLayout(fps_row)
        fps_layout.setContentsMargins(0, 0, 0, 0)
        fps_layout.setSpacing(6)
        self.auto_fps_cb = QtWidgets.QCheckBox("Auto")
        self.auto_fps_cb.setChecked(True)
        self.auto_fps_cb.setToolTip(
            "Use 30 fps, or the Lottie file's native rate for JSON inputs."
        )
        fps_layout.addWidget(self.fps_edit)
        fps_layout.addWidget(self.auto_fps_cb)

        frames_row = QtWidgets.QWidget()
        frames_layout = QtWidgets.QHBoxLayout(frames_row)
        frames_layout.setContentsMargins(0, 0, 0, 0)
        frames_layout.setSpacing(6)
        self.auto_frames_cb = QtWidgets.QCheckBox("Auto")
        self.auto_frames_cb.setChecked(True)
        self.auto_frames_cb.setToolTip(
            "Use the animation's native loop length "
            "(Lottie: frame count from the JSON)."
        )
        frames_layout.addWidget(self.frames_edit)
        frames_layout.addWidget(self.auto_frames_cb)

        self.output_dir_edit = QtWidgets.QLineEdit("")
        self.output_dir_edit.setPlaceholderText("(default: next to source)")
        output_row = QtWidgets.QWidget()
        output_layout = QtWidgets.QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(6)
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output_dir)
        output_layout.addWidget(self.output_dir_edit)
        output_layout.addWidget(browse_btn)

        self.meta_label = QtWidgets.QLabel("")
        if lottie_meta:
            self.meta_label.setText(
                f"Lottie native: {lottie_meta['totalFrames']} frames @ "
                f"{lottie_meta['frameRate']} fps"
            )
            self.meta_label.setStyleSheet("color: #8af;")

        self.uv_pass_cb = QtWidgets.QCheckBox("UV Pass")
        self.id_pass_cb = None if batch_mode else QtWidgets.QCheckBox("Object ID Pass")
        self.skip_existing_cb = (
            QtWidgets.QCheckBox("Skip existing") if batch_mode else None
        )

        layout.addRow("Width", self.width_edit)
        layout.addRow("Height", self.height_edit)
        layout.addRow("Frames", frames_row)
        layout.addRow("FPS", fps_row)
        layout.addRow("Output dir", output_row)
        if lottie_meta:
            layout.addRow("", self.meta_label)
        layout.addRow("", self.uv_pass_cb)
        if self.id_pass_cb:
            layout.addRow("", self.id_pass_cb)
        if self.skip_existing_cb:
            layout.addRow("", self.skip_existing_cb)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.auto_fps_cb.toggled.connect(self._on_auto_fps_toggled)
        self.auto_frames_cb.toggled.connect(self._on_auto_frames_toggled)
        self._on_auto_fps_toggled(self.auto_fps_cb.isChecked())
        self._on_auto_frames_toggled(self.auto_frames_cb.isChecked())

    def _browse_output_dir(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory")
        if path:
            self.output_dir_edit.setText(path)

    def _format_fps(self, fps):
        fps = float(fps)
        return str(int(fps) if fps.is_integer() else fps)

    def _auto_fps_text(self):
        if self._lottie_meta:
            return self._format_fps(self._lottie_meta["frameRate"])
        return self._format_fps(self._default_fps)

    def _on_auto_fps_toggled(self, checked):
        self.fps_edit.setEnabled(not checked)
        if checked:
            self.fps_edit.setText(self._auto_fps_text())

    def _on_auto_frames_toggled(self, checked):
        self.frames_edit.setEnabled(not checked)
        if checked and self._lottie_meta:
            self.frames_edit.setText(str(self._lottie_meta["totalFrames"]))

    def values(self):
        values = {
            "width": self.width_edit.text(),
            "height": self.height_edit.text(),
            "frames": self.frames_edit.text(),
            "fps": self.fps_edit.text(),
            "auto_frames": self.auto_frames_cb.isChecked(),
            "auto_fps": self.auto_fps_cb.isChecked(),
            "uv_pass": self.uv_pass_cb.isChecked(),
            "id_pass": self.id_pass_cb.isChecked() if self.id_pass_cb else False,
            "output_dir": self.output_dir_edit.text().strip(),
        }
        if self.skip_existing_cb:
            values["skip_existing"] = self.skip_existing_cb.isChecked()
        return values


class _RasterizeProgressDialog(QtWidgets.QDialog):
    """Modeless panel that runs svg_to_frames.py without blocking Nuke."""

    _PASS_LABELS = {
        "color": "Color",
        "uv": "UV",
        "id": "Object ID",
    }

    def __init__(self, cmd, job):
        parent = nuke.mainWindow() if hasattr(nuke, "mainWindow") else None
        super().__init__(parent)
        self._job = job
        self._passes = []
        self._total_frames = 0
        self._finished = False

        batch_idx = job.get("batch_index")
        batch_total = job.get("batch_total")
        if batch_idx is not None and batch_total:
            title = f"Batch rasterize ({batch_idx + 1}/{batch_total})"
        elif job.get("rerender"):
            title = "Re-rasterizing SVG"
        else:
            title = "Rasterizing SVG"
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.setWindowModality(QtCore.Qt.NonModal)

        layout = QtWidgets.QVBoxLayout(self)

        self.status_label = QtWidgets.QLabel("Starting…")
        self.pass_label = QtWidgets.QLabel("")
        self.file_label = QtWidgets.QLabel("")
        self.file_label.setStyleSheet("color: #aaa;")
        self.file_label.setWordWrap(True)
        if job.get("src"):
            self.file_label.setText(os.path.basename(job["src"]))

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

    def _notify_complete(self, success):
        cb = self._job.get("on_complete")
        if cb:
            cb(success, self._job)

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
        self._notify_complete(False)
        self._cleanup()

    def _cancel(self):
        if self._process.state() != QtCore.QProcess.NotRunning:
            self._process.kill()
        self.status_label.setText("Cancelled.")
        self._notify_complete(False)
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
            if self._job.get("batch_index") is None:
                nuke.message(f"SVG rasterization failed (exit {exit_code}):\n{detail}")
            else:
                nuke.tprint(
                    f"Batch item failed ({os.path.basename(self._job.get('src', ''))}): "
                    f"exit {exit_code}"
                )
                self._append_log(f"FAILED: {detail}")
            self._notify_complete(False)
            self._cleanup()
            return

        job = self._job
        out_dir = job["out_dir"]

        if not os.path.isdir(out_dir):
            msg = "No output folder was produced — check the script output above."
            if job.get("batch_index") is None:
                nuke.message(msg)
            else:
                nuke.tprint(msg)
            self._notify_complete(False)
            self._cleanup()
            return

        frames = _find_frames(out_dir, job["base"])
        if not frames:
            msg = "No frames were produced — check the script output above."
            if job.get("batch_index") is None:
                nuke.message(msg)
            else:
                nuke.tprint(msg)
            self._notify_complete(False)
            self._cleanup()
            return

        try:
            _finish_import_nodes(job, frames)
        except RuntimeError as exc:
            if job.get("batch_index") is None:
                nuke.message(str(exc))
            else:
                nuke.tprint(str(exc))
            self._notify_complete(False)
            self._cleanup()
            return

        self.status_label.setText("Done.")
        self.progress.setValue(100)
        self._notify_complete(True)
        self._cleanup()


class _BatchImportCoordinator:
    """Serial batch queue — one rasterize job at a time (T-3b)."""

    def __init__(self, files, opts, output_root=None):
        self.files = files
        self.opts = opts
        self.output_root = (output_root or "").strip() or None
        self.index = 0
        self._failures = []
        self._start_next()

    def _start_next(self):
        if self.index >= len(self.files):
            if self._failures:
                nuke.message(
                    f"Batch import finished with {len(self._failures)} failure(s):\n"
                    + "\n".join(self._failures)
                )
            else:
                nuke.message(f"Batch import complete — {len(self.files)} file(s).")
            return

        src = self.files[self.index]

        def on_complete(success, _job):
            if not success:
                self._failures.append(os.path.basename(src))
            self.index += 1
            self._start_next()

        _start_rasterize_job(
            src,
            self.opts,
            output_dir=self.output_root,
            batch_index=self.index,
            batch_total=len(self.files),
            layout_offset_y=self.index * 150,
            on_complete=on_complete,
        )


def import_animated_svg():
    src = nuke.getFilename("Select animated SVG / Lottie JSON / HTML", "*.svg *.json *.html")
    if not src:
        return

    lottie_meta = _read_lottie_metadata(src)
    panel = _ImportAnimatedSvgPanel(30.0, lottie_meta)
    if panel.exec_() != QtWidgets.QDialog.Accepted:
        return

    opts = panel.values()
    _start_rasterize_job(src, opts)


def batch_import_animated_svg():
    folder = QtWidgets.QFileDialog.getExistingDirectory(
        nuke.mainWindow() if hasattr(nuke, "mainWindow") else None,
        "Select folder of SVG / Lottie / HTML files",
    )
    if not folder:
        return

    panel = _ImportAnimatedSvgPanel(30.0, lottie_meta=None, batch_mode=True)
    if panel.exec_() != QtWidgets.QDialog.Accepted:
        return

    opts = panel.values()
    output_root = opts.get("output_dir") or None
    files = _discover_batch_files(
        folder,
        skip_existing=opts.get("skip_existing", False),
        output_root=output_root,
    )
    if not files:
        nuke.message("No importable files found in the selected folder.")
        return

    _BatchImportCoordinator(files, opts, output_root=output_root)


def reimport_selected_read():
    """Re-rasterize from metadata stored on a color Read node (T-5)."""
    selected = nuke.selectedNodes("Read")
    if len(selected) != 1:
        nuke.message("Select exactly one SVG color Read node to re-render.")
        return

    color_read = selected[0]
    meta = _get_svg_metadata(color_read)
    if not meta:
        nuke.message(
            "Selected Read has no SVG import metadata.\n"
            "Re-render is only available on Reads created by Import Animated SVG."
        )
        return

    src = meta["source"]
    if not os.path.isfile(src):
        nuke.message(f"Source file not found:\n{src}")
        return

    basename = os.path.basename(src)
    if not nuke.ask(f"Re-rasterize {basename}?\nThis overwrites existing frames."):
        return

    opts = {
        "width": meta["width"],
        "height": meta["height"],
        "frames": meta["frames"],
        "fps": meta["fps"],
        "auto_frames": meta["auto_frames"],
        "auto_fps": meta["auto_fps"],
        "uv_pass": meta["uv_pass"],
        "id_pass": meta["id_pass"],
    }
    out_pattern = meta["out_pattern"]
    output_dir = os.path.dirname(out_pattern) if out_pattern else None
    siblings = _find_import_siblings(color_read)

    _start_rasterize_job(
        src,
        opts,
        output_dir=output_dir,
        rerender=True,
        existing_nodes=siblings,
    )


def install():
    file_menu = nuke.menu("Nuke").findItem("File")
    file_menu.addCommand("Import Animated SVG...", import_animated_svg)
    file_menu.addCommand("Batch Import Animated SVG...", batch_import_animated_svg)

    nodes_menu = nuke.menu("Nuke").findItem("Nodes")
    if nodes_menu:
        nodes_menu.addCommand("Re-render SVG Source", reimport_selected_read)
