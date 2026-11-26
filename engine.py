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


def _dpi_q(s: float):
    """Quality knob s in [0,1] -> (max DPI, JPEG quality). Higher = better & bigger."""
    dpi = int(50 * (12 ** s))          # 50 .. 600 DPI
    q = int(round(30 + 70 * s))        # 30 .. 100
    return dpi, q


class _Img:
    __slots__ = ("obj", "inches", "pil", "gray", "w", "h", "raw_len", "cache", "keep", "boost")


def _collect(pdf: pikepdf.Pdf):
    items = []
    for obj, inches in list(_iter_images(pdf)):
        try:
            if obj.get(Name.ImageMask, False) or int(obj.get(Name.BitsPerComponent, 8)) == 1:
                continue
            filt = obj.get(Name.Filter)
            fl = [str(f) for f in filt] if isinstance(filt, pikepdf.Array) else [str(filt)] if filt else []
            if any(f in ("/JBIG2Decode", "/CCITTFaxDecode", "/JPXDecode") for f in fl):
                continue
            w, h = int(obj.Width), int(obj.Height)
            if w * h < 64 * 64:
                continue
            pil = PdfImage(obj).as_pil_image()
            if pil.mode not in ("L", "RGB"):
                pil = pil.convert("L" if pil.mode in ("1", "LA", "I", "I;16", "F") else "RGB")
            it = _Img()
            it.obj, it.inches, it.pil, it.gray = obj, inches, pil, pil.mode == "L"
            it.w, it.h, it.raw_len, it.cache, it.keep, it.boost = w, h, len(obj.read_raw_bytes()), {}, False, False
            items.append(it)
        except Exception:
            continue
    return items


def _encode(it: _Img, s: float):
    """Return (jpeg_bytes or None to keep original, new_size)."""
    if it.keep:
        return (None, (it.w, it.h))
    if it.boost:
        s = 1.0
    dpi, q = _dpi_q(s)
    max_px = int(it.inches * dpi)
    scale = min(1.0, max_px / max(it.w, it.h))
    if scale >= 0.98:
        scale = 1.0
    size = (max(1, int(it.w * scale)), max(1, int(it.h * scale)))
    key = (size, q)
    if key in it.cache:
        return it.cache[key]
    pil = it.pil if scale == 1.0 else it.pil.resize(size, Image.LANCZOS)
    buf = io.BytesIO()
    pil.save(buf, "JPEG", quality=q, optimize=True, progressive=True,
             subsampling=0 if q >= 100 else 2)
    data = buf.getvalue()
    res = (None, size) if (scale == 1.0 and len(data) >= it.raw_len) else (data, size)
    it.cache.clear()
    it.cache[key] = res
    return res


def _estimate(items, overhead: int, s: float) -> int:
    total = overhead
    for it in items:
        data, _ = _encode(it, s)
        total += it.raw_len if data is None else len(data)
    return total


def _apply(src_path: str, dst: str, s: float, keep=frozenset(), boost=frozenset()) -> int:
    with pikepdf.open(src_path) as pdf:
        for idx, it in enumerate(_collect(pdf)):
            it.keep = idx in keep
            it.boost = idx in boost
            data, size = _encode(it, s)
            if data is None:
                continue
            obj = it.obj
            obj.write(data, filter=Name.DCTDecode)
            obj.Width, obj.Height = size
            obj.ColorSpace = Name.DeviceGray if it.gray else Name.DeviceRGB
            obj.BitsPerComponent = 8
            for k in (Name.DecodeParms, Name.Decode):
                if k in obj:
                    del obj[k]
            smask = obj.get(Name.SMask)
            if smask is not None and size != (it.w, it.h):
                try:
                    m = PdfImage(smask).as_pil_image().convert("L").resize(size, Image.LANCZOS)
                    smask.write(zlib.compress(m.tobytes(), 9), filter=Name.FlateDecode)
                    smask.Width, smask.Height = size
                    smask.BitsPerComponent = 8
                    smask.ColorSpace = Name.DeviceGray
                    if Name.DecodeParms in smask:
                        del smask[Name.DecodeParms]
                except Exception:
                    del obj[Name.SMask]
        return _save(pdf, dst)


def _label(s: float) -> str:
    dpi, q = _dpi_q(s)
    if s >= 0.85:
        name = "Original-like"
    elif s >= 0.65:
        name = "Excellent"
    elif s >= 0.45:
        name = "Very high"
    elif s >= 0.3:
        name = "High"
    else:
        name = "Good"
    return f"{name} (up to {min(dpi, 600)} DPI, {q}% JPEG)"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
FILL = 0.985  # aim for 98.5% of the chosen size (e.g. 1 MB -> ~1000 KB, never above)


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
        report(5, "Cleaning up PDF (lossless)...")
        lossless = os.path.join(tmpdir, "lossless.pdf")
        with pikepdf.open(src) as pdf:
            _lossless_cleanup(pdf)
            size = _save(pdf, lossless)
        best_path, best_size = (lossless, size) if size < in_size else (src, in_size)

        if best_size <= target_bytes:
            shutil.copyfile(best_path, dst)
            report(100, "Done")
            msg = ("File is already smaller than the chosen size - kept 100% original quality."
                   if in_size <= target_bytes else
                   "Fits without touching any pixel - 100% original quality.")
            return Result(True, in_size, best_size, target_bytes, "lossless",
                          "100% original quality", dst, msg)

        report(12, "Reading images...")
        with pikepdf.open(lossless) as pdf:
            items = _collect(pdf)
            overhead = size - sum(it.raw_len for it in items)
            goal = int(target_bytes * FILL)

            if not items or _estimate(items, overhead, 0.0) > target_bytes:
                # Not enough images to shrink: give the smallest clean version.
                out = os.path.join(tmpdir, "min.pdf")
                s_min = _apply(lossless, out, 0.0) if items else best_size
                if items and s_min < best_size:
                    best_path, best_size = out, s_min
                shutil.copyfile(best_path, dst)
                report(100, "Done")
                return Result(False, in_size, best_size, target_bytes, "best-possible",
                              _label(0.0) if items else "100% original quality", dst,
                              "This PDF is mostly text/fonts and cannot reach the chosen size "
                              "without damaging them. Saved the smallest clean version.")

            def search(ovh):
                if _estimate(items, ovh, 1.0) <= goal:
                    return 1.0
                lo, hi = 0.0, 1.0
                for _ in range(12):
                    mid = (lo + hi) / 2
                    if _estimate(items, ovh, mid) <= goal:
                        lo = mid
                    else:
                        hi = mid
                return lo

            # Write, measure the real size, correct the estimate and repeat.
            out = os.path.join(tmpdir, "out.pdf")
            best = None
            ovh = overhead
            for attempt in range(4):
                report(20 + attempt * 14, f"Finding best quality for {human(target_bytes)}...")
                s = search(ovh)
                keep, boost = set(), set()
                # Spend leftover room: upgrade single images to max JPEG, then to original.
                for it in items:
                    it.keep = it.boost = False
                est = _estimate(items, ovh, s)
                def cost(i):
                    it = items[i]
                    return it.raw_len
                for idx in sorted(range(len(items)), key=cost):
                    it = items[idx]
                    cur = len(_encode(it, s)[0] or b"") or it.raw_len
                    if s < 1.0:
                        it.boost = True
                        up = len(_encode(it, 1.0)[0] or b"") or it.raw_len
                        if est - cur + up <= goal:
                            est, cur = est - cur + up, up
                            boost.add(idx)
                        else:
                            it.boost = False
                    if est - cur + it.raw_len <= goal:
                        est += it.raw_len - cur
                        keep.add(idx)
                for it in items:
                    it.keep = it.boost = False
                cand = os.path.join(tmpdir, f"try{attempt}.pdf")
                real = _apply(lossless, cand, s, frozenset(keep), frozenset(boost))
                if real <= target_bytes and (best is None or real > best[0]):
                    best = (real, cand, s, len(keep))
                if real <= target_bytes and (real >= goal * 0.93 or s >= 1.0):
                    break
                # correct overhead by the estimation error and retry
                for idx, it in enumerate(items):
                    it.keep, it.boost = idx in keep, idx in boost
                est = _estimate(items, ovh, s)
                for it in items:
                    it.keep = it.boost = False
                ovh += real - est

        if best is None:  # safety net: lowest quality
            cand = os.path.join(tmpdir, "low.pdf")
            best = (_apply(lossless, cand, 0.0), cand, 0.0, 0)
        real, cand, s, kept = best
        shutil.copyfile(cand, dst)
        report(100, "Done")
        if kept and kept == len(items):
            lbl = "100% original quality"
        elif kept:
            lbl = f"Original-like ({kept} image(s) untouched)"
        else:
            lbl = _label(s)
        msg = "Text, fonts and lines untouched; images at the highest quality that fits."
        if s >= 1.0 and real < goal * 0.9:
            msg = ("Already at maximum image quality - it does not need the full "
                   f"{human(target_bytes)}. Text and fonts untouched.")
        return Result(real <= target_bytes, in_size, real, target_bytes, "image-optimized",
                      lbl, dst, msg)
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
