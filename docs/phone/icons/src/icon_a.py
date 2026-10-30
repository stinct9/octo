import sys; sys.path.insert(0, sys.argv[1])  # usage: python icon_a.py <dir with octo.py> <out dir>
from octo import octopus, arc, bbox
OUT = sys.argv[2]

def pixel_icon(variant="light", arcs=True, r=10, phase=0.8, span_k=0.76, gap=0.04):
    cells = octopus(0, 0, r, 1.0, False, phase, eyes=False)
    # The eyes: the firmware's cut-out, rounded (its ellipse test leaves a nub at each of the four points,
    # which reads as a cog at icon size), with a 2x2 pupil (its 1-radius disc reads as a "+").
    ex, ey, er = (r * 2) // 5, r // 6, r // 4 + 1
    for s in (-1, 1):
        for j in range(-er, er + 1):
            for i in range(-er, er + 1):
                if i * i + j * j <= er * er - 2.5: cells.discard((s * ex + i, ey + j))  # round: no nubs at the four points
        for (i, j) in ((0, -1), (1, -1), (0, 0), (1, 0)): cells.add((s * ex + i, ey + j))
    if arcs:
        for k in range(2):
            cells |= arc(0, 0, r + 4 + 3 * k, 236, 304)
    x0, y0, x1, y1 = bbox(cells)
    gw, gh = x1 - x0 + 1, y1 - y0 + 1
    span = span_k * 1024                     # the octopus spans 70% of the icon's width
    cell = span / max(gw, gh * 1.0)
    ox = (1024 - gw * cell) / 2 - x0 * cell
    oy = (1024 - gh * cell) / 2 - y0 * cell + 0.01 * 1024
    inset = cell * gap
    rx = cell * 0.22
    rects = ''.join(f'<rect x="{ox + x * cell + inset:.1f}" y="{oy + y * cell + inset:.1f}" width="{cell - 2 * inset:.1f}" height="{cell - 2 * inset:.1f}" rx="{rx:.1f}"/>' for x, y in sorted(cells))
    if variant == 'tinted':  # iOS tints a greyscale image: white mark on black, no colour
        bg = '<rect width="1024" height="1024" fill="#000"/>'
        return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
{bg}<g fill="#FFFFFF">{rects}</g></svg>'''
    if variant == 'dark':
        top, bot, glowc, halo, sheen = '#08141D', '#020507', '#6FB8F0', 0.22, 0.035
    else:
        top, bot, glowc, halo, sheen = '#163A57', '#060D13', '#8FD0FF', 0.42, 0.07
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" width="1024" height="1024">
<defs>
  <linearGradient id="glass" x1="0" y1="0" x2="0.35" y2="1">
    <stop offset="0" stop-color="{top}"/><stop offset="1" stop-color="{bot}"/>
  </linearGradient>
  <radialGradient id="halo" cx="0.5" cy="0.52" r="0.48">
    <stop offset="0" stop-color="#2E7DB8" stop-opacity="{halo}"/><stop offset="1" stop-color="#2E7DB8" stop-opacity="0"/>
  </radialGradient>
  <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#FFFFFF" stop-opacity="{sheen}"/><stop offset="0.45" stop-color="#FFFFFF" stop-opacity="0"/>
  </linearGradient>
  <filter id="bloom" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="{cell * 0.75:.1f}"/></filter>
</defs>
<rect width="1024" height="1024" fill="url(#glass)"/>
<rect width="1024" height="1024" fill="url(#halo)"/>
<g fill="{glowc}" opacity="0.55" filter="url(#bloom)">{rects}</g>
<g fill="#EAF6FF">{rects}</g>
<rect width="1024" height="1024" fill="url(#sheen)"/>
</svg>'''

open(f'{OUT}/icon-a.svg', 'w').write(pixel_icon('light'))
open(f'{OUT}/icon-a-dark.svg', 'w').write(pixel_icon('dark'))
open(f'{OUT}/icon-a-tinted.svg', 'w').write(pixel_icon('tinted'))
