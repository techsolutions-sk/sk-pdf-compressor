"""Creates sample PDFs for CI screenshots."""
import io, sys
import numpy as np
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

for name, pages in (("Invoice_Report_2026.pdf", 4), ("Scanned_Documents.pdf", 2)):
    c = canvas.Canvas(name)
    for p in range(pages):
        c.drawString(50, 800, f"Tech Solutions sample page {p + 1}")
        y, x = np.mgrid[0:1500, 0:2000] / 2000
        a = np.stack([128 + 100 * np.sin(6 * x + p), 128 + 90 * np.cos(5 * y), 128 + 60 * np.sin(4 * (x + y))], -1)
        a += np.random.default_rng(p).normal(0, 8, a.shape)
        b = io.BytesIO(); Image.fromarray(np.clip(a, 0, 255).astype("uint8")).save(b, "PNG"); b.seek(0)
        c.drawImage(ImageReader(b), 50, 200, width=495, height=371)
        c.showPage()
    c.save()
print("samples ok")
