"""Generates app.ico, app.png and MSIX Assets (run before build)."""
import os
from PIL import Image, ImageDraw, ImageFont


def icon(S):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    s = S / 256
    d.rounded_rectangle([8*s, 8*s, 248*s, 248*s], radius=48*s, fill="#1E3A8A")
    d.rounded_rectangle([62*s, 34*s, 174*s, 186*s], radius=10*s, fill="white")
    d.polygon([(146*s, 34*s), (174*s, 62*s), (146*s, 62*s)], fill="#BFDBFE")
    d.rectangle([80*s, 70*s, 150*s, 82*s], fill="#DC2626")
    for y in (100, 120, 140):
        d.rectangle([80*s, y*s, 156*s, (y+8)*s], fill="#94A3B8")
    d.polygon([(150*s, 150*s), (222*s, 150*s), (186*s, 190*s)], fill="#FBBF24")
    d.polygon([(150*s, 236*s), (222*s, 236*s), (186*s, 196*s)], fill="#FBBF24")
    font = None
    for f in ("arialbd.ttf", "C:/Windows/Fonts/arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(f, int(56*s)); break
        except Exception:
            pass
    d.text((40*s, 186*s), "SK", font=font or ImageFont.load_default(), fill="white")
    return im


base = icon(512)
b256 = base.resize((256, 256), Image.LANCZOS)
b256.save("app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
b256.save("app.png")
os.makedirs("Assets", exist_ok=True)
for n, sz in [("StoreLogo.png", 50), ("Square44x44Logo.png", 44), ("Square150x150Logo.png", 150),
              ("Square44x44Logo.targetsize-256_altform-unplated.png", 256)]:
    base.resize((sz, sz), Image.LANCZOS).save("Assets/" + n)
w = Image.new("RGBA", (310, 150), "#1E3A8A")
small = base.resize((120, 120), Image.LANCZOS)
w.paste(small, (95, 15), small)
w.save("Assets/Wide310x150Logo.png")
print("icons ok")
