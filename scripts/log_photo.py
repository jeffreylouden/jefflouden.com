#!/usr/bin/env python3
"""Prepares a photo for the log: upright, at most 1600px on its long side, a
plain JPEG with every byte of metadata gone (a phone keeps where a photo was
taken in its EXIF).

    python3 scripts/log_photo.py photo.jpg public/images/log/2026-10-24-boat.jpg

Needs Pillow (pip install pillow), like the résumé needs pypdf. build.py
itself needs nothing: it only checks that the photos it finds were prepared.
"""

import io
import sys

MAX_SIDE = 1600
QUALITY = 82


def prepare(data):
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as e:
        raise ValueError(f'that is not a photo Pillow can read (JPEG, PNG or WebP): {e}') from None
    image = ImageOps.exif_transpose(image)
    if image.mode != 'RGB':
        background = Image.new('RGB', image.size, (255, 255, 255))
        image = image.convert('RGBA')
        background.paste(image, mask=image.getchannel('A'))
        image = background
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    out = io.BytesIO()
    # A fresh image carries no info, so nothing (EXIF, XMP, ICC, comments) is written but the pixels.
    clean = Image.new('RGB', image.size)
    clean.paste(image)
    clean.save(out, 'JPEG', quality=QUALITY, optimize=True, progressive=True)
    return out.getvalue()


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__.split('\n\n')[1])
    with open(sys.argv[1], 'rb') as f:
        data = prepare(f.read())
    with open(sys.argv[2], 'wb') as f:
        f.write(data)
    print(f'wrote {sys.argv[2]} ({len(data) // 1000} KB)')


if __name__ == '__main__':
    main()
