#!/usr/bin/env python3
"""
photo_to_face.py - swap the background photo inside a captured Revoltt FS1 face file.

Replaces ONLY the 240x284 RGB565 (big-endian) image block at the offset bgproto.py
found (0x4B00 in our sample), leaving every header, descriptor and glyph asset byte
identical. This is the smallest possible edit, which is why it's the first one to try.

Usage:
  python3 photo_to_face.py clean.bin my_photo.jpg -o patched.bin
  python3 photo_to_face.py clean.bin my_photo.jpg -o patched.bin --preview preview.png
  python3 photo_to_face.py clean.bin my_photo.jpg -o patched.bin --fit cover --safe-box 0,0,240,120

Then hand patched.bin to bgproto.py to turn it into a write sequence:
  python3 bgproto.py build patched.bin -o writes.txt
"""
import argparse, struct
from PIL import Image
import numpy as np
import bgproto as bg


def to_rgb565_be(img: Image.Image) -> bytes:
    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    r = (a[..., 0] >> 3) & 0x1F
    g = (a[..., 1] >> 2) & 0x3F
    b = (a[..., 2] >> 3) & 0x1F
    px = (r << 11) | (g << 5) | b
    return px.astype(">u2").tobytes()


def fit_image(img: Image.Image, w: int, h: int, mode: str) -> Image.Image:
    if mode == "stretch":
        return img.resize((w, h), Image.LANCZOS)
    src_ratio, dst_ratio = img.width / img.height, w / h
    if mode == "cover":
        if src_ratio > dst_ratio:
            new_h = h; new_w = round(h * src_ratio)
        else:
            new_w = w; new_h = round(w / src_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        x0, y0 = (new_w - w) // 2, (new_h - h) // 2
        return img.crop((x0, y0, x0 + w, y0 + h))
    if mode == "contain":
        if src_ratio > dst_ratio:
            new_w = w; new_h = round(w / src_ratio)
        else:
            new_h = h; new_w = round(h * src_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        canvas.paste(img, ((w - new_w) // 2, (h - new_h) // 2))
        return canvas
    raise ValueError(f"unknown --fit {mode}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("face", help="a known-good reassembled face file, e.g. clean.bin")
    p.add_argument("photo")
    p.add_argument("-o", required=True)
    p.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover")
    p.add_argument("--preview", help="also save a PNG preview of the patched image")
    p.add_argument("--safe-box", help="x,y,w,h to darken/outline, e.g. where time/date/steps get drawn "
                                       "(unconfirmed — see README) so you can sanity-check overlap")
    a = p.parse_args()

    face = bytearray(open(a.face, "rb").read())
    img_off = bg.image_offset(bytes(face))

    photo = Image.open(a.photo)
    fitted = fit_image(photo, bg.IMG_W, bg.IMG_H, a.fit)
    raw = to_rgb565_be(fitted)
    assert len(raw) == bg.IMG_W * bg.IMG_H * 2

    face[img_off:img_off + len(raw)] = raw
    open(a.o, "wb").write(face)
    print(f"wrote {a.o}: replaced {len(raw)} bytes at 0x{img_off:X}, rest of file unchanged")

    if a.preview:
        prev = fitted.copy()
        if a.safe_box:
            from PIL import ImageDraw
            x, y, w, h = map(int, a.safe_box.split(","))
            d = ImageDraw.Draw(prev)
            d.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=2)
        prev.save(a.preview)
        print(f"wrote {a.preview}")


if __name__ == "__main__":
    main()
