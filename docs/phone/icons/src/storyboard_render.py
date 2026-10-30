import sys
from PIL import Image, ImageDraw
lines = [l.split() for l in open(sys.argv[1]) if l.strip()]
S = 4; W, H = 128 * S, 64 * S; PAD = 14; CAP = 26
cols = 4; rows = (len(lines) + cols - 1) // cols
sheet = Image.new('RGB', (cols * (W + PAD) + PAD, rows * (H + PAD + CAP) + PAD), (236, 232, 226))
d = ImageDraw.Draw(sheet)
for k, (name, hexs) in enumerate(lines):
    fb = bytes.fromhex(hexs)
    im = Image.new('RGB', (W, H), (5, 9, 12)); g = ImageDraw.Draw(im)
    for y in range(64):
        for x in range(128):
            if fb[(y // 8) * 128 + x] >> (y & 7) & 1: g.rectangle([x * S + 1, y * S + 1, x * S + S - 1, y * S + S - 1], fill=(232, 244, 255))
    ox = PAD + (k % cols) * (W + PAD); oy = PAD + (k // cols) * (H + PAD + CAP)
    sheet.paste(im, (ox, oy + CAP))
    d.text((ox, oy + 6), name.replace('__', ' · ').replace('_', ' '), fill=(30, 30, 30))
sheet.save(sys.argv[2])
