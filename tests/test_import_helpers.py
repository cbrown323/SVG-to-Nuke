#!/usr/bin/env python3
"""Unit tests for import helpers (no Nuke required)."""
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load_nuke_module():
    """Load nuke_svg_import with a mocked nuke API."""
    fake_nuke = types.ModuleType("nuke")

    class FakeKnob:
        def __init__(self, value=0):
            self._value = value
            self._flags = 0

        def setValue(self, v):
            self._value = v

        def value(self):
            return self._value

        def setFlag(self, flag):
            self._flags |= flag

        def fromUserText(self, _text):
            pass

    class FakeNode:
        _next_id = 0

        def __init__(self, klass):
            FakeNode._next_id += 1
            self.klass = klass
            self._name = f"{klass}{FakeNode._next_id}"
            self.inputs = {}
            self.knobs = {
                "xpos": FakeKnob(0),
                "ypos": FakeKnob(0),
                "label": FakeKnob(""),
                "file": FakeKnob(""),
                "first": FakeKnob(1),
                "last": FakeKnob(1),
                "origfirst": FakeKnob(1),
                "origlast": FakeKnob(1),
            }

        def name(self):
            return self._name

        def __getitem__(self, key):
            return self.knobs[key]

        def knob(self, key):
            return self.knobs.get(key)

        def addKnob(self, knob):
            self.knobs[knob.name()] = knob

        def setInput(self, index, node):
            self.inputs[index] = node

        def input(self, index):
            return self.inputs.get(index)

        def Class(self):
            return self.klass

    class FakeStringKnob(FakeKnob):
        def __init__(self, name, label, value=""):
            super().__init__(value)
            self._name = name
            self._label = label

        def name(self):
            return self._name

    def create_node(klass):
        return FakeNode(klass)

    fake_nuke.createNode = create_node
    fake_nuke.String_Knob = FakeStringKnob
    fake_nuke.INVISIBLE = 2
    fake_nuke.tprint = print
    fake_nuke.mainWindow = lambda: None
    fake_nuke.message = print
    fake_nuke.ask = lambda msg: True
    fake_nuke.getFilename = lambda *a, **k: None
    fake_nuke.selectedNodes = lambda *a, **k: []
    fake_nuke.allNodes = lambda *a, **k: []
    fake_nuke.toNode = lambda name: None
    fake_nuke.menu = lambda *a, **k: types.SimpleNamespace(
        findItem=lambda *a, **k: types.SimpleNamespace(addCommand=lambda *a, **k: None)
    )

    class _Stub:
        pass

    qt_widgets = types.ModuleType("QtWidgets")
    for name in (
        "QDialog", "QFormLayout", "QLineEdit", "QWidget", "QHBoxLayout",
        "QCheckBox", "QLabel", "QDialogButtonBox", "QVBoxLayout",
        "QProgressBar", "QPlainTextEdit", "QPushButton", "QFileDialog",
    ):
        setattr(qt_widgets, name, _Stub)
    qt_widgets.QDialogButtonBox = type(
        "QDialogButtonBox", (_Stub,),
        {"Ok": 1, "Cancel": 2},
    )
    qt_widgets.QFormLayout = type(
        "QFormLayout", (_Stub,),
        {"FieldsStayAtSizeHint": 0},
    )
    qt_widgets.QFileDialog = type(
        "QFileDialog", (_Stub,),
        {"getExistingDirectory": staticmethod(lambda *a, **k: "")},
    )

    qt = types.ModuleType("PySide2")
    qt.QtCore = types.SimpleNamespace(
        Qt=types.SimpleNamespace(NonModal=0),
        QProcess=type(
            "QProcess", (_Stub,),
            {
                "MergedChannels": 0,
                "NotRunning": 0,
                "ProcessError": types.SimpleNamespace(Crashed=0),
            },
        ),
    )
    qt.QtWidgets = qt_widgets

    with mock.patch.dict(
        sys.modules,
        {
            "nuke": fake_nuke,
            "PySide2": qt,
            "PySide2.QtCore": qt.QtCore,
            "PySide2.QtWidgets": qt_widgets,
        },
    ):
        spec = importlib.util.spec_from_file_location(
            "nuke_svg_import", ROOT / "nuke_svg_import.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod, FakeNode, fake_nuke


def test_resolve_output_paths_default():
    mod, _, _ = _load_nuke_module()
    base, out_dir, pattern = mod._resolve_output_paths("/tmp/rocket.svg")
    assert base == "rocket"
    assert out_dir == os.path.join("/tmp", "rocket_frames")
    assert pattern == os.path.join("/tmp", "rocket_frames", "rocket.####.png")


def test_resolve_output_paths_custom_dir():
    mod, _, _ = _load_nuke_module()
    base, out_dir, pattern = mod._resolve_output_paths(
        "/tmp/rocket.svg", output_dir="/renders/out"
    )
    assert base == "rocket"
    assert out_dir == "/renders/out"
    assert pattern == os.path.join("/renders/out", "rocket.####.png")


def test_discover_batch_files():
    mod, _, _ = _load_nuke_module()
    with tempfile.TemporaryDirectory() as tmp:
        open(os.path.join(tmp, "a.svg"), "w").close()
        open(os.path.join(tmp, "b.json"), "w").close()
        open(os.path.join(tmp, "_skip.svg"), "w").close()
        open(os.path.join(tmp, "note.txt"), "w").close()
        found = mod._discover_batch_files(tmp)
        assert found == [
            os.path.join(tmp, "a.svg"),
            os.path.join(tmp, "b.json"),
        ]


def test_discover_batch_files_skip_existing():
    mod, _, _ = _load_nuke_module()
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "anim.svg")
        open(src, "w").close()
        frames_dir = os.path.join(tmp, "anim_frames")
        os.makedirs(frames_dir)
        open(os.path.join(frames_dir, "anim.0001.png"), "w").close()
        found = mod._discover_batch_files(tmp, skip_existing=True)
        assert found == []


def test_metadata_round_trip():
    mod, FakeNode, _ = _load_nuke_module()
    color = FakeNode("Read")
    opts = {
        "width": "1920",
        "height": "1080",
        "fps": "30",
        "frames": "72",
        "auto_frames": True,
        "auto_fps": True,
        "uv_pass": True,
        "id_pass": False,
    }
    uv = FakeNode("Read")
    mod._stamp_read_metadata(
        color, "/tmp/rocket.svg", opts, "/tmp/rocket_frames/rocket.####.png", uv_read=uv
    )
    meta = mod._get_svg_metadata(color)
    assert meta is not None
    assert meta["source"] == "/tmp/rocket.svg"
    assert meta["width"] == "1920"
    assert meta["uv_pass"] is True
    assert meta["id_pass"] is False
    assert meta["out_pattern"] == "/tmp/rocket_frames/rocket.####.png"
    assert meta["uv_read_name"] == uv.name()


def test_build_raster_cmd_includes_out():
    mod, _, _ = _load_nuke_module()
    opts = {
        "width": "512",
        "height": "512",
        "fps": "24",
        "frames": "60",
        "auto_frames": True,
        "auto_fps": False,
        "uv_pass": True,
        "id_pass": False,
    }
    cmd = mod._build_raster_cmd(
        "/tmp/a.svg", "/out/a.####.png", opts
    )
    assert "--out" in cmd
    out_idx = cmd.index("--out")
    assert cmd[out_idx + 1] == "/out/a.####.png"
    assert "--uv-pass" in cmd
    assert "--auto-frames" in cmd


if __name__ == "__main__":
    test_resolve_output_paths_default()
    test_resolve_output_paths_custom_dir()
    test_discover_batch_files()
    test_discover_batch_files_skip_existing()
    test_metadata_round_trip()
    test_build_raster_cmd_includes_out()
    print("Import helper tests OK")
