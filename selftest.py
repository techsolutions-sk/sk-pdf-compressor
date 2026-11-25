"""Quick engine self-test used by the CI build."""
import io, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from engine import TARGETS, compress, human

d = tempfile.mkdtemp()
src = os.path.join(d, "in.pdf")
c = canvas.Canvas(src)
rng = np.random.default_rng(0)
for p in range(3):
    c.drawString(50, 800, f"SK PDF Compressor self-test page {p + 1}")
    y, x = np.mgrid[0:1500, 0:2000] / 2000
    arr = np.stack([128 + 100 * np.sin(6 * x + p), 128 + 90 * np.cos(5 * y), 128 + 60 * np.sin(4 * (x + y))], -1)
    arr += rng.normal(0, 8, arr.shape)
    buf = io.BytesIO(); Image.fromarray(np.clip(arr, 0, 255).astype("uint8")).save(buf, "PNG"); buf.seek(0)
    c.drawImage(ImageReader(buf), 50, 200, width=495, height=371)
    c.showPage()
c.save()
print("input", human(os.path.getsize(src)))
fail = False
for label, t in TARGETS:
    r = compress(src, os.path.join(d, f"out_{t}.pdf"), t)
    print(f"{label:>7}: {human(r.output_size):>9}  {r.mode:16} {r.level}")
    if r.mode == "error" or (r.ok and r.output_size > t):
        fail = True
sys.exit(1 if fail else 0)
