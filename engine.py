"""
SK PDF Compressor - compression engine
Tech Solutions

Strategy (best quality first):
  1. Lossless pass  - recompress streams, object streams, drop unused objects,
                      thumbnails & duplicate images. Pixels are NOT touched.
  2. If the file still does not fit the chosen size, images are re-encoded
     starting from the HIGHEST quality level and stepping down only as far as
     needed. Text, fonts and vector graphics are never changed, so they stay
     100% sharp at every level.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tempfile
import zlib
from dataclasses import dataclass
from typing import Callable, Optional

import pikepdf
from pikepdf import Name, PdfImage
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# The 5 size options shown in the app
TARGETS = [
    ("500 KB", 500 * 1024),
    ("1 MB", 1 * 1024 * 1024),
    ("2 MB", 2 * 1024 * 1024),
    ("5 MB", 5 * 1024 * 1024),
    ("10 MB", 10 * 1024 * 1024),
]

# Image quality ladder: (max DPI, JPEG quality, label). Tried from top to
# bottom; the first level that fits the target size wins.
LEVELS = [
    (1200, 97, "Maximum (native resolution)"),
    (300, 95, "Original-like (300 DPI)"),
    (300, 90, "Excellent (300 DPI)"),
    (250, 88, "Excellent (250 DPI)"),
    (220, 85, "Very high (220 DPI)"),
    (200, 82, "Very high (200 DPI)"),
    (180, 80, "High (180 DPI)"),
    (150, 80, "High (150 DPI)"),
    (150, 72, "Good (150 DPI)"),
    (130, 70, "Good (130 DPI)"),
    (110, 65, "Screen (110 DPI)"),
    (96, 60, "Screen (96 DPI)"),
    (85, 55, "Small (85 DPI)"),
    (72, 50, "Smallest (72 DPI)"),
]


@dataclass
class Result:
    ok: bool
    input_size: int
    output_size: int
    target: int
    mode: str  # "lossless" | "image-optimized" | "best-possible" | "error"
    level: str
    output_path: str
    message: str = ""

    @property
    def saved_percent(self) -> float:
        if not self.input_size:
            return 0.0
        return max(0.0, (1 - self.output_size / self.input_size) * 100)


def human(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.2f} MB"
    return f"{n / 1024:.0f} KB"


# --------------------------------------------------------------------------- #
# Lossless pass
# --------------------------------------------------------------------------- #
def _save(pdf: pikepdf.Pdf, path: str) -> int:
    pdf.remove_unreferenced_resources()
    pdf.save(
        path,
        compress_streams=True,
        recompress_flate=True,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
        stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
        linearize=False,
    )
    return os.path.getsize(path)


def _lossless_cleanup(pdf: pikepdf.Pdf) -> None:
    # Page thumbnails are never visible in the document itself.
    for page in pdf.pages:
        if Name.Thumb in page.obj:
            del page.obj[Name.Thumb]
    # Merge byte-identical images (common in scanned / generated PDFs).
    seen: dict[str, pikepdf.Object] = {}
    for page in pdf.pages:
        res = page.obj.get(Name.Resources)
        if res is None or Name.XObject not in res:
            continue
        xobjs = res[Name.XObject]
        for key in list(xobjs.keys()):
            obj = xobjs[key]
            try:
                if obj.get(Name.Subtype) != Name.Image:
                    continue
                digest = hashlib.sha1(
                    obj.read_raw_bytes() + repr(sorted(
                        (str(k), str(v)) for k, v in obj.items()
                        if k not in ("/Length",)
                    )).encode()
                ).hexdigest()
            except Exception:
                continue
            if digest in seen:
                xobjs[key] = seen[digest]
            else:
                seen[digest] = obj


# --------------------------------------------------------------------------- #
# Image optimisation pass
# --------------------------------------------------------------------------- #
def _iter_images(pdf: pikepdf.Pdf):
    """Yield (image_object, page_long_side_in_inches) once per image."""
    done = set()

    def walk(res, page_inches, depth=0):
        if res is None or depth > 6 or Name.XObject not in res:
            return
        for _, obj in res[Name.XObject].items():
            try:
                og = obj.objgen
            except Exception:
                continue
            sub = obj.get(Name.Subtype)
            if sub == Name.Image:
                if og in done:
                    continue
                done.add(og)
                yield obj, page_inches
            elif sub == Name.Form:
                yield from walk(obj.get(Name.Resources), page_inches, depth + 1)

    for page in pdf.pages:
        try:
            box = page.mediabox
            w = abs(float(box[2]) - float(box[0])) / 72.0
            h = abs(float(box[3]) - float(box[1])) / 72.0
            inches = max(w, h) or 11.7
        except Exception:
            inches = 11.7
        yield from walk(page.obj.get(Name.Resources), inches)


def _optimise_image(obj: pikepdf.Object, page_inches: float, dpi: int, quality: int) -> None:
    if obj.get(Name.ImageMask, False):
        return
    bpc = int(obj.get(Name.BitsPerComponent, 8))
    if bpc == 1:
        return  # 1-bit scans (CCITT/JBIG2) are already tiny & sharp
    filt = obj.get(Name.Filter)
    filters = [str(f) for f in filt] if isinstance(filt, pikepdf.Array) else [str(filt)] if filt else []
    if any(f in ("/JBIG2Decode", "/CCITTFaxDecode", "/JPXDecode") for f in filters):
        return

    w, h = int(obj.Width), int(obj.Height)
    if w * h < 64 * 64:
        return  # icons / tiny images: leave untouched

    try:
        pil = PdfImage(obj).as_pil_image()
    except Exception:
        return

    if pil.mode in ("1", "P", "LA", "PA", "RGBA", "I", "I;16", "F"):
        pil = pil.convert("L" if pil.mode in ("1", "LA", "I", "I;16", "F") else "RGB")
    gray = pil.mode == "L"
    if pil.mode not in ("L", "RGB"):
        pil = pil.convert("RGB")

    max_px = int(page_inches * dpi)
    scale = min(1.0, max_px / max(w, h))
    if scale < 0.98:
        pil = pil.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=quality, optimize=True, progressive=True,
             subsampling=0 if quality >= 88 else 2)
    data = buf.getvalue()

    old_len = len(obj.read_raw_bytes())
    if len(data) >= old_len and scale >= 0.98:
        return  # never make an image bigger

    obj.write(data, filter=Name.DCTDecode)
    obj.Width, obj.Height = pil.size
    obj.ColorSpace = Name.DeviceGray if gray else Name.DeviceRGB
    obj.BitsPerComponent = 8
    for k in (Name.DecodeParms, Name.Decode):
        if k in obj:
            del obj[k]

    # Resize soft mask (transparency) to match, losslessly.
    smask = obj.get(Name.SMask)
    if smask is not None and scale < 0.98:
        try:
            m = PdfImage(smask).as_pil_image().convert("L")
            m = m.resize(pil.size, Image.LANCZOS)
            smask.write(zlib.compress(m.tobytes(), 9), filter=Name.FlateDecode)
            smask.Width, smask.Height = m.size
            smask.BitsPerComponent = 8
            smask.ColorSpace = Name.DeviceGray
            if Name.DecodeParms in smask:
                del smask[Name.DecodeParms]
        except Exception:
            del obj[Name.SMask]


def _build_level(src: str, dst: str, dpi: int, quality: int) -> int:
    with pikepdf.open(src) as pdf:
        _lossless_cleanup(pdf)
        for obj, inches in list(_iter_images(pdf)):
            try:
                _optimise_image(obj, inches, dpi, quality)
            except Exception:
                continue
        return _save(pdf, dst)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def compress(
    src: str,
    dst: str,
    target_bytes: int,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Result:
    def report(p, msg):
        if progress:
            progress(p, msg)

    in_size = os.path.getsize(src)
    tmpdir = tempfile.mkdtemp(prefix="skpdf_")
    try:
        report(5, "Lossless optimisation…")
        lossless = os.path.join(tmpdir, "lossless.pdf")
        with pikepdf.open(src) as pdf:
            _lossless_cleanup(pdf)
            size = _save(pdf, lossless)

        best_path, best_size = (lossless, size) if size < in_size else (src, in_size)

        if best_size <= target_bytes:
            shutil.copyfile(best_path, dst)
            return Result(True, in_size, best_size, target_bytes, "lossless",
                          "100% original quality", dst,
                          "No quality change at all — only internal data cleaned up.")

        # Binary search over the quality ladder for the best level that fits.
        lo, hi = 0, len(LEVELS) - 1
        found = None
        tried = {}
        step = 0
        total_steps = max(1, len(LEVELS).bit_length() + 1)
        while lo <= hi:
            mid = (lo + hi) // 2
            dpi, q, label = LEVELS[mid]
            step += 1
            report(10 + int(80 * step / total_steps), f"Trying {label}…")
            out = os.path.join(tmpdir, f"lvl{mid}.pdf")
            s = _build_level(src, out, dpi, q)
            tried[mid] = (out, s)
            if s <= target_bytes:
                found = mid
                hi = mid - 1  # try a better quality
            else:
                lo = mid + 1

        if found is not None:
            out, s = tried[found]
            shutil.copyfile(out, dst)
            report(100, "Done")
            return Result(True, in_size, s, target_bytes, "image-optimized",
                          LEVELS[found][2], dst,
                          "Text & fonts untouched; images kept at the highest quality that fits.")

        # Could not reach the target: give the smallest we managed.
        smallest_idx = min(tried, key=lambda i: tried[i][1])
        out, s = tried[smallest_idx]
        if s >= best_size:
            out, s = best_path, best_size
        shutil.copyfile(out, dst)
        report(100, "Done")
        return Result(False, in_size, s, target_bytes, "best-possible",
                      LEVELS[smallest_idx][2] if out != best_path else "100% original quality",
                      dst,
                      "This PDF cannot go below the chosen size without damaging text/fonts. "
                      "Saved the smallest clean version.")
    except pikepdf.PasswordError:
        return Result(False, in_size, in_size, target_bytes, "error", "", dst,
                      "This PDF is password-protected. Please unlock it first.")
    except Exception as e:  # pragma: no cover
        return Result(False, in_size, in_size, target_bytes, "error", "", dst, f"Error: {e}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def output_name(src: str, target_label: str) -> str:
    base, _ = os.path.splitext(src)
    tag = target_label.replace(" ", "")
    candidate = f"{base}_compressed_{tag}.pdf"
    i = 2
    while os.path.exists(candidate):
        candidate = f"{base}_compressed_{tag}_{i}.pdf"
        i += 1
    return candidate
