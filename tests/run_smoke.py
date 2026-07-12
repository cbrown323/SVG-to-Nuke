#!/usr/bin/env python3
"""Automated smoke test — no Nuke required.

Runs a minimal Lottie + SVG/UV matrix and checks exit codes, frame counts,
and UV alpha parity (F-1 regression guard).
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "svg_to_frames.py"
SAMPLE_LOTTIE = ROOT / "test_assets" / "sample_bounce.json"
ROCKET_SVG = ROOT / "test_assets" / "rocket_test.svg"

sys.path.insert(0, str(ROOT / "test_assets"))
from check_alpha import compare_alpha_passes  # noqa: E402


def _run(cmd):
    print(f"\n>>> {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result


def _count_frames(out_dir, base_name):
    return len(list(Path(out_dir).glob(f"{base_name}.0*.png")))


def test_lottie_color_only(out_dir):
    out_pattern = str(out_dir / "sample.####.png")
    cmd = [
        sys.executable,
        str(SCRIPT),
        str(SAMPLE_LOTTIE),
        "--out",
        out_pattern,
        "--auto-frames",
        "--width",
        "256",
        "--height",
        "256",
    ]
    result = _run(cmd)
    if result.returncode != 0:
        return False, f"Lottie render failed (exit {result.returncode})"

    expected = 30  # sample_bounce.json: op-ip = 30 @ 30fps
    written = _count_frames(out_dir, "sample")
    if written != expected:
        return False, f"Expected {expected} Lottie frames, got {written}"

    first = out_dir / "sample.0001.png"
    if not first.is_file() or first.stat().st_size < 100:
        return False, f"Missing or empty frame: {first}"

    log_path = out_dir / "render.log"
    if not log_path.is_file():
        return False, f"Missing render.log in {out_dir}"

    return True, f"Lottie OK — {written} frames, render.log present"


def test_svg_uv_alpha(out_dir):
    out_pattern = str(out_dir / "rocket.####.png")
    cmd = [
        sys.executable,
        str(SCRIPT),
        str(ROCKET_SVG),
        "--out",
        out_pattern,
        "--auto-frames",
        "--uv-pass",
        "--width",
        "256",
        "--height",
        "256",
    ]
    result = _run(cmd)
    if result.returncode != 0:
        return False, f"SVG+UV render failed (exit {result.returncode})"

    color_count = _count_frames(out_dir, "rocket")
    uv_count = len(list(out_dir.glob("rocket.uv.0*.png")))
    if color_count < 1:
        return False, "No color frames written"
    if uv_count != color_count:
        return False, f"Color/UV frame mismatch: {color_count} vs {uv_count}"

    worst, per_frame = compare_alpha_passes(out_dir, "rocket")
    if not per_frame:
        return False, "No frames to compare for alpha parity"
    if worst >= 0.1:
        return False, f"UV alpha parity failed — worst frame {worst:.3f}% > 0.1%"

    log_path = out_dir / "render.log"
    if not log_path.is_file():
        return False, f"Missing render.log in {out_dir}"

    return True, f"SVG+UV OK — {color_count} frames, alpha worst {worst:.3f}%"


def main():
    if not SCRIPT.is_file():
        sys.exit(f"Missing rasterizer: {SCRIPT}")

    try:
        import playwright  # noqa: F401
    except ImportError:
        sys.exit(
            "Playwright not installed. Run:\n"
            "  pip install -r requirements.txt\n"
            "  playwright install chromium"
        )

    cases = []
    with tempfile.TemporaryDirectory(prefix="svg_nuke_smoke_") as tmp:
        tmp_path = Path(tmp)
        lottie_dir = tmp_path / "lottie"
        uv_dir = tmp_path / "uv"
        lottie_dir.mkdir()
        uv_dir.mkdir()

        ok, msg = test_lottie_color_only(lottie_dir)
        cases.append(("Lottie color", ok, msg))

        ok, msg = test_svg_uv_alpha(uv_dir)
        cases.append(("SVG + UV alpha", ok, msg))

    print("\n=== Smoke test summary ===")
    failed = 0
    for name, ok, msg in cases:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name}: {msg}")
        if not ok:
            failed += 1

    if failed:
        sys.exit(f"{failed} case(s) failed")
    print("\nSMOKE OK")
    return 0


if __name__ == "__main__":
    main()
