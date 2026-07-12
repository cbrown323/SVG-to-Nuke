#!/usr/bin/env python3
"""Unit test for STMap graph wiring (no Nuke required)."""
import importlib.util
import sys
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

        def setValue(self, v):
            self._value = v

        def value(self):
            return self._value

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

        def setInput(self, index, node):
            self.inputs[index] = node

    def create_node(klass):
        return FakeNode(klass)

    fake_nuke.createNode = create_node
    fake_nuke.tprint = print
    fake_nuke.mainWindow = lambda: None
    fake_nuke.message = print
    fake_nuke.getFilename = lambda *a, **k: None
    fake_nuke.menu = lambda *a, **k: types.SimpleNamespace(
        findItem=lambda *a, **k: types.SimpleNamespace(addCommand=lambda *a, **k: None)
    )

    class _Stub:
        pass

    qt_widgets = types.ModuleType("QtWidgets")
    for name in (
        "QDialog", "QFormLayout", "QLineEdit", "QWidget", "QHBoxLayout",
        "QCheckBox", "QLabel", "QDialogButtonBox", "QVBoxLayout",
        "QProgressBar", "QPlainTextEdit", "QPushButton",
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
        return mod, FakeNode


def test_build_stmap_graph_wires_inputs_and_layout():
    mod, FakeNode = _load_nuke_module()
    color = FakeNode("Read")
    color["xpos"].setValue(100)
    color["ypos"].setValue(200)
    uv = FakeNode("Read")

    stmap = mod._build_stmap_graph(color, uv, label="rocket")

    assert stmap.klass == "STMap"
    assert stmap.inputs[0] is color
    assert stmap.inputs[1] is uv
    assert stmap["xpos"].value() == 100
    assert stmap["ypos"].value() == 280
    assert stmap["label"].value() == "rocket"


def test_build_stmap_graph_skips_without_uv():
    mod, FakeNode = _load_nuke_module()
    color = FakeNode("Read")
    assert mod._build_stmap_graph(color, None) is None


if __name__ == "__main__":
    test_build_stmap_graph_wires_inputs_and_layout()
    test_build_stmap_graph_skips_without_uv()
    print("STMap graph tests OK")
