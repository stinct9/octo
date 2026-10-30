import math, sys
OUT = sys.argv[1]  # usage: python icon_bc.py <out dir>
f1 = lambda v: f'{v:.1f}'

# ---- B: the landing page's octopus (docs/landing/src/app.js, class Octopus), drawn once ----
def landing_octopus(cx, cy, R, ln, arms=8, splay=0.62, phase=1.3, look=(0.25, -0.15)):
    base = cy + R * 0.58
    paths = []
    for i in range(arms):
        u = (i + 0.5) / arms; out = (u - 0.5) * 2
        L = ln * (1 - 0.2 * out * out); wig = 0.32; N = 18; ds = L / N; p = phase
        x = cx - R * 0.84 + 2 * R * 0.84 * u; y = base - 8; a = 0
        pts = [(x, y)]; w0 = (2 * R * 0.84 / arms) * 1.7; w1 = max(2.6, R * 0.088); ws = [w0]
        for k in range(1, N + 1):
            s = k / N
            a = math.pi / 2 - out * splay + wig * s * math.sin(p * 1.7 + s * 3.4 + i * 1.13)
            x += ds * math.cos(a); y += ds * math.sin(a); pts.append((x, y)); ws.append(w1 + (w0 - w1) * (1 - s) ** 1.25)
        turn = -1 if out >= 0 else 1; M = 11
        for m in range(1, M + 1):
            f = m / M; a += turn * (0.38 + 0.04 * math.sin(p + i)); step = ds * 0.72 * (1 - 0.55 * f)
            x += step * math.cos(a); y += step * math.sin(a); pts.append((x, y)); ws.append(w1 * (1 - 0.62 * f))
        left, right = [], []
        for k in range(len(pts)):
            a0 = pts[max(0, k - 1)]; a1 = pts[min(len(pts) - 1, k + 1)]
            tx, ty = a1[0] - a0[0], a1[1] - a0[1]; tl = math.hypot(tx, ty) or 1; tx /= tl; ty /= tl; h = ws[k] / 2
            left.append((pts[k][0] - ty * h, pts[k][1] + tx * h)); right.append((pts[k][0] + ty * h, pts[k][1] - tx * h))
        ring = left + right[::-1]
        mid = lambda a, b: ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        m0 = mid(ring[-1], ring[0]); d = f'M{f1(m0[0])} {f1(m0[1])}'
        for k in range(len(ring)):
            c = ring[k]; nx = mid(c, ring[(k + 1) % len(ring)]); d += f'Q{f1(c[0])} {f1(c[1])} {f1(nx[0])} {f1(nx[1])}'
        paths.append(d + 'Z')
    body = f'M{f1(cx - R)} {f1(cy)} A{f1(R)} {f1(R)} 0 0 1 {f1(cx + R)} {f1(cy)} L{f1(cx + R)} {f1(base)} L{f1(cx - R)} {f1(base)} Z'
    ey = cy + R * 0.12
    eyes = ''
    for s in (-1, 1):
        x = cx + s * R * 0.37; px, py = x + look[0] * R * 0.06, ey + look[1] * R * 0.07
        eyes += (f'<ellipse cx="{f1(x)}" cy="{f1(ey)}" rx="{f1(R * 0.165)}" ry="{f1(R * 0.2)}" fill="#FFFFFF"/>'
                 f'<circle cx="{f1(px)}" cy="{f1(py)}" r="{f1(R * 0.1)}" fill="#0A1822"/>'
                 f'<circle cx="{f1(px - R * 0.032)}" cy="{f1(py - R * 0.038)}" r="{f1(R * 0.034)}" fill="#FFFFFF"/>')
    cheeks = ''.join(f'<ellipse cx="{f1(cx + s * R * 0.62)}" cy="{f1(ey + R * 0.24)}" rx="{f1(R * 0.13)}" ry="{f1(R * 0.075)}" fill="#F09AA2" opacity="0.7"/>' for s in (-1, 1))
    shine = f'<ellipse cx="{f1(cx - R * 0.36)}" cy="{f1(cy - R * 0.46)}" rx="{f1(R * 0.36)}" ry="{f1(R * 0.22)}" transform="rotate(-28 {f1(cx - R * 0.36)} {f1(cy - R * 0.46)})" fill="#FFFFFF" opacity="0.16"/>'
    return paths, body, shine + cheeks + eyes

def icon_b():
    arms, body, face = landing_octopus(512, 420, 250, 300)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
<defs><linearGradient id="tank" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F8FBFA"/><stop offset="1" stop-color="#CFE1DF"/></linearGradient></defs>
<rect width="1024" height="1024" fill="url(#tank)"/>
<g fill="#1D4F7A">{''.join(f'<path d="{d}"/>' for d in arms)}<path d="{body}"/></g>
{face}
</svg>'''

# ---- C: the recorder's octopus (octo_display.h), drawn smooth: six arms, cut-out eyes, listening ----
def oled_vector(cx, cy, r, unit, phase=0.8, fg='#EAF6FF', bg='#1D4F7A', arcs_col='#7FE0CF', arcs=3):
    U = lambda v: v * unit
    out_svg = []
    base = cy + r * 2 / 3
    # arms: the firmware's centre line, k = 0 .. len, as smooth strokes that thin towards the tip
    for i in range(6):
        x0 = cx - r + 1 + (2.0 * r - 2) * (i + 0.5) / 6
        out = (i - 2.5) * 0.45
        ln = r - (2 if i in (0, 5) else 0)
        ks = [j / 4 for j in range(-6, ln * 4 + 1)]  # from inside the mantle, so the joins don't show
        pts = [(x0 + out * max(k, 0) + 1.6 * math.sin(phase + k * 0.45 + i * 1.1), base + k) for k in ks]
        # one outline that thins from 3 units to 1.9 (the screen's 3 px then 2 px), round at the tip
        left, right = [], []
        for n, (x, y) in enumerate(pts):
            a0 = pts[max(0, n - 1)]; a1 = pts[min(len(pts) - 1, n + 1)]
            tx_, ty_ = a1[0] - a0[0], a1[1] - a0[1]; tl = math.hypot(tx_, ty_) or 1; tx_ /= tl; ty_ /= tl
            t = max(0.0, ks[n]) / ln; h = (3.0 - 1.1 * t ** 1.4) / 2
            left.append((x - ty_ * h, y + tx_ * h)); right.append((x + ty_ * h, y - tx_ * h))
        ring = left + right[::-1]
        d = 'M' + ' L'.join(f'{f1(U(x))} {f1(U(y))}' for x, y in ring) + 'Z'
        out_svg.append(f'<path d="{d}" fill="{fg}" stroke="{fg}" stroke-width="{f1(U(0.2))}" stroke-linejoin="round"/>')
        tx, ty = pts[-1]
        out_svg.append(f'<circle cx="{f1(U(tx))}" cy="{f1(U(ty))}" r="{f1(U(1.55))}" fill="{fg}"/>')
    # head and mantle
    out_svg.append(f'<path d="M{f1(U(cx - r - 0.5))} {f1(U(cy))} A{f1(U(r + 0.5))} {f1(U(r + 0.5))} 0 0 1 {f1(U(cx + r + 0.5))} {f1(U(cy))} L{f1(U(cx + r + 0.5))} {f1(U(base + 0.6))} L{f1(U(cx - r - 0.5))} {f1(U(base + 0.6))} Z" fill="{fg}"/>')
    # eyes cut out of the head, pupils looking slightly right as on the screen
    ex, ey, er = r * 2 / 5, cy + r / 6, r / 4 + 1
    for s in (-1, 1):
        out_svg.append(f'<ellipse cx="{f1(U(cx + s * ex))}" cy="{f1(U(ey))}" rx="{f1(U(er))}" ry="{f1(U(er))}" fill="{bg}"/>')
        out_svg.append(f'<circle cx="{f1(U(cx + s * ex + 0.8))}" cy="{f1(U(ey - 0.2))}" r="{f1(U(er * 0.46))}" fill="{fg}"/>')
    # listening: the screen's sound arcs, 225..315 degrees, radius r+4, +4
    for k in range(arcs):
        rr = r + 4 + 3.6 * k
        a0, a1 = math.radians(232), math.radians(308)
        out_svg.append(f'<path d="M{f1(U(cx + rr * math.cos(a0)))} {f1(U(cy + rr * math.sin(a0)))} A{f1(U(rr))} {f1(U(rr))} 0 0 1 {f1(U(cx + rr * math.cos(a1)))} {f1(U(cy + rr * math.sin(a1)))}" fill="none" stroke="{arcs_col}" stroke-width="{f1(U(1.7))}" stroke-linecap="round" opacity="{1 - 0.22 * k:.2f}"/>')
    return ''.join(out_svg)

def icon_c(variant='light'):
    if variant == 'dark':
        bg_top, bg_bot, flat, fg, arcs = '#0B1A26', '#03080C', '#071119', '#D9ECFA', '#66CDBD'
    else:
        bg_top, bg_bot, flat, fg, arcs = '#25608F', '#123754', '#1A4A70', '#F2F8FC', '#7FE0CF'
    # in OLED units: r = 14 as on the recording screen; centred, ~0.74 of the width
    # bounding box in OLED units (r = 14, centre 0,0): arms reach about -26..+26, arcs top -21.6, tips +25
    r = 14
    xs, ys = [], []
    for i in range(6):
        x0 = -r + 1 + (2.0 * r - 2) * (i + 0.5) / 6; out = (i - 2.5) * 0.45; ln = r - (2 if i in (0, 5) else 0)
        for k in range(ln + 1):
            xs.append(x0 + out * k + 1.6 * math.sin(0.8 + k * 0.45 + i * 1.1)); ys.append(r * 2 / 3 + k)
    xmin, xmax = min(xs) - 1.6, max(xs) + 1.6
    ymin, ymax = -(r + 4 + 3.6) - 1, max(ys) + 1.6
    unit = 1024 * 0.80 / max(xmax - xmin, ymax - ymin)
    cx = (1024 / unit - (xmax + xmin)) / 2
    cy = (1024 / unit - (ymax + ymin)) / 2
    art = oled_vector(cx, cy, r, unit, phase=0.8, fg=fg, bg=flat, arcs_col=arcs, arcs=2)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{bg_top}"/><stop offset="1" stop-color="{bg_bot}"/></linearGradient></defs>
<rect width="1024" height="1024" fill="url(#bg)"/>
{art.replace(f'fill="{flat}"', 'fill="url(#bg)"')}
</svg>'''

open(f'{OUT}/icon-b.svg', 'w').write(icon_b())
open(f'{OUT}/icon-c.svg', 'w').write(icon_c())
