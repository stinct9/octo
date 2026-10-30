"""A review sheet: each icon large, then at 120 and 60 px with an iOS-like mask, on light and dark wallpapers."""
import sys
from PIL import Image, ImageDraw, ImageFilter
out, files = sys.argv[1], sys.argv[2:]
def masked(im, size):
    im = im.convert('RGB').resize((size, size), Image.LANCZOS)
    m = Image.new('L', (size * 4, size * 4), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size * 4 - 1, size * 4 - 1], radius=int(size * 4 * 0.2237), fill=255)
    m = m.resize((size, size), Image.LANCZOS)
    return im, m
rows = len(files)
W, H = 1120, 300 * rows
sheet = Image.new('RGB', (W, H), (128, 128, 128))
d = ImageDraw.Draw(sheet)
for k, f in enumerate(files):
    im = Image.open(f)
    y = k * 300
    d.rectangle([0, y, 560, y + 299], fill=(236, 232, 226))
    d.rectangle([560, y, W, y + 299], fill=(24, 26, 30))
    big, m = masked(im, 260); sheet.paste(big, (20, y + 20), m)
    for bx, bg in ((300, 0), (860, 1)):
        a, ma = masked(im, 120); sheet.paste(a, (bx if bg == 0 else 600, y + 30), ma)
        b, mb = masked(im, 60); sheet.paste(b, ((bx if bg == 0 else 600) + 140, y + 60), mb)
        c, mc = masked(im, 40); sheet.paste(c, ((bx if bg == 0 else 600) + 140, y + 160), mc)
    big2, m2 = masked(im, 260); sheet.paste(big2, (840, y + 20), m2)
    d.text((300, y + 270), f.split('/')[-1], fill=(40, 40, 40))
sheet.save(out)
