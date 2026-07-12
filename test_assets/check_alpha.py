"""Compare alpha coverage between color and UV pass frames."""
import sys
from pathlib import Path

from PIL import Image, ImageChops


def compare_alpha_passes(out_dir, base_name, tolerance_pct=0.1, alpha_threshold=8):
    """Compare color vs UV alpha masks frame-by-frame.

    Returns (worst_pct, per_frame_stats) where worst_pct is the maximum
    percentage of pixels whose alpha differs by more than alpha_threshold/255.
    """
    out_dir = Path(out_dir)
    worst = 0.0
    per_frame = []
    color_re = f"{base_name}."
    for color_path in sorted(out_dir.glob(f"{base_name}.0*.png")):
        frame = color_path.name.split(".")[1]
        uv_path = out_dir / f"{base_name}.uv.{frame}.png"
        if not uv_path.is_file():
            raise FileNotFoundError(f"Missing UV frame: {uv_path}")
        a_color = Image.open(color_path).getchannel("A")
        a_uv = Image.open(uv_path).getchannel("A")
        diff = ImageChops.difference(a_color, a_uv)
        hist = diff.histogram()
        total = sum(hist)
        bad = sum(hist[alpha_threshold + 1 :])
        pct = 100.0 * bad / total if total else 0.0
        worst = max(worst, pct)
        max_diff = max(i for i, c in enumerate(hist) if c)
        per_frame.append(
            {
                "frame": frame,
                "bad_pixels": bad,
                "total_pixels": total,
                "pct": pct,
                "max_diff": max_diff,
            }
        )
    return worst, per_frame


def main():
    out = Path(__file__).parent / "out"
    worst, per_frame = compare_alpha_passes(out, "rocket")
    for stats in per_frame:
        print(
            f"frame {stats['frame']}: {stats['bad_pixels']}/{stats['total_pixels']} "
            f"pixels differ >8/255 ({stats['pct']:.3f}%), "
            f"max diff {stats['max_diff']}"
        )
    print(f"worst frame: {worst:.3f}% mismatched pixels")
    sys.exit(0 if worst < 0.1 else 1)


if __name__ == "__main__":
    main()
