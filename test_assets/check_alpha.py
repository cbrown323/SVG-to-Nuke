"""Compare alpha coverage between color and UV pass frames."""
import sys
from pathlib import Path

from PIL import Image, ImageChops

out = Path(__file__).parent / "out"
worst = 0.0
for color_path in sorted(out.glob("rocket.0*.png")):
    frame = color_path.name.split(".")[1]
    uv_path = out / f"rocket.uv.{frame}.png"
    a_color = Image.open(color_path).getchannel("A")
    a_uv = Image.open(uv_path).getchannel("A")
    diff = ImageChops.difference(a_color, a_uv)
    hist = diff.histogram()
    total = sum(hist)
    # pixels whose alpha differs by more than 8/255 (AA tolerance)
    bad = sum(hist[9:])
    pct = 100.0 * bad / total
    worst = max(worst, pct)
    print(f"frame {frame}: {bad}/{total} pixels differ >8/255 ({pct:.3f}%), max diff {max(i for i, c in enumerate(hist) if c)}")

print(f"worst frame: {worst:.3f}% mismatched pixels")
sys.exit(0 if worst < 0.1 else 1)
