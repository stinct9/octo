"""The recorder's octopus (octo_display.h od_octopus_eyes), as a set of lit cells, for icon work."""
import math

def lround(v): return -math.floor(-v + 0.5) if v < 0 else math.floor(v + 0.5)

def octopus(cx, cy, r, open_=1.0, alert=False, phase=0.0, eyes=True):
    on = set()
    def px(x, y, v):
        if v: on.add((x, y))
        else: on.discard((x, y))
    def fill(x, y, w, h, v):
        for j in range(y, y + h):
            for i in range(x, x + w): px(i, j, v)
    def disc(cx_, cy_, rr, v):
        for j in range(-rr, rr + 1):
            for i in range(-rr, rr + 1):
                if i * i + j * j <= rr * rr + rr // 2: px(cx_ + i, cy_ + j, v)
    disc(cx, cy, r, 1)
    fill(cx - r, cy, 2 * r + 1, (r * 2) // 3, 1)
    base = cy + (r * 2) // 3
    for i in range(6):
        x0 = cx - r + 1 + (2.0 * r - 2) * (i + 0.5) / 6
        out = (i - 2.5) * 0.45
        ln = r - (2 if i in (0, 5) else 0)
        for k in range(ln):
            x = x0 + out * k + 1.6 * math.sin(phase + k * 0.45 + i * 1.1)
            fill(lround(x) - 1, base + k, 3 - (1 if k > (ln * 2) // 3 else 0), 1, 1)
        disc(lround(x0 + out * ln + 1.6 * math.sin(phase + ln * 0.45 + i * 1.1)), base + ln, 1, 1)
    if eyes:
        ex, ey, er = (r * 2) // 5, cy + r // 6, r // 4 + 1
        for s in (-1, 1):
            if open_ < 0.2:
                for i in range(-er, er + 1): px(cx + s * ex + i, ey + (i * i) // (er + 1) - 1, 0)
                for i in range(-er, er + 1): px(cx + s * ex + i, ey + (i * i) // (er + 1), 0)
            else:
                ry = er * min(open_, 1)
                for j in range(-er, er + 1):
                    for i in range(-er, er + 1):
                        if i * i / (er * er) + j * j / (ry * ry) <= 1.05: px(cx + s * ex + i, ey + j, 0)
                if open_ > 0.6: disc(cx + s * ex + (0 if alert else 1), ey + (-1 if alert else 0), er // 2, 1)
    return on

def arc(cx, cy, r, a0, a1):
    pts = set()
    for a in range(a0, a1 + 1, 2):
        pts.add((cx + lround(r * math.cos(a * 0.0174533)), cy + lround(r * math.sin(a * 0.0174533))))
    return pts

def bbox(cells):
    xs = [c[0] for c in cells]; ys = [c[1] for c in cells]
    return min(xs), min(ys), max(xs), max(ys)
