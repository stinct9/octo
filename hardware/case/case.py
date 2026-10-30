"""Octo's table case, concept v1 (6 Oct 2026): a parametric model, not yet for printing.

The owner's design (sketch, 6 Oct): two levels. M1, the XIAO ESP32S3 Sense, stands on EB, the XIAO
expansion board, so that end is taller; EB's OLED shows through the top of the lower level; the
controls are on the sides and press the boards' own buttons and switch. Every number below says
where it came from: positions on EB from Seeed's PCB layout (15 Oct); the ones marked CHECK are still
estimates. The recording light is the screen: no LED is added (15 Oct). Reasoning and the print plan: hardware/case/README.md.

    pip install manifold3d numpy
    python3 hardware/case/case.py        # writes out/: the STLs, deck.svg and the review page

Units: mm. Case coordinates: x along the long side (USB-C end at x = 0), y from front to back,
z up from the bottom of the case (the stick-on feet add about 2.5 mm).
"""
from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path

import numpy as np
from manifold3d import CrossSection, JoinType, Manifold, OpType

SEG = 64  # segments in a full circle

# ---- the boards: published numbers ---------------------------------------------------------------
EB_L, EB_W = 58.0, 42.5       # Seeed Expansion Board Base for XIAO (103030356), as DigiKey lists it
EB_T = 1.6                    # PCB thickness: the usual 1.6 mm (assumed)
EB_R = 1.5                    # its corner radius: CHECK (a sharper corner needs a smaller R_PLAN)
XIAO_L, XIAO_W = 21.0, 17.8   # XIAO ESP32S3: Seeed's KiCad footprint, 20.98 x 17.8
STACK_H = 15.0                # XIAO + Sense board, Seeed: "21 x 17.5 x 15 mm (with expansion board)"
SOCKET_H = 11.0               # EB's female headers (8.5) + the XIAO's header spacer (2.5): usual parts, CHECK
OLED_AA = (21.74, 10.86)      # 0.96" 128x64 OLED active area: the usual panel (assumed)
OLED_GLASS = (24.74, 16.9)    # its glass: Seeed's PCB layout (U6); measured 25 x 17-18 (15 Oct)
OLED_TOP = 2.0                # glass surface above EB's top: CHECK

# ---- where things sit on EB: Seeed's PCB layout v1.0 (Seeeduino XIAO Expansion board_v1.0_200824.brd,
# from their wiki), read 15 Oct; the owner's measurements agree with it ----------------------------------
# EB coordinates: origin at EB's front-left corner, x along the 58 mm side, y along the 42.5 mm side.
XIAO_C = (10.54, 21.25)       # centre of M1 (U2); its USB-C faces the left edge
OLED_C = (34.8, 21.25)        # centre of the OLED glass (U6)
SWITCH_X = 15.0               # slide switch (SW1, battery on/off) on the back edge; measured: lever at 13 on, 15.5 off
RESET_X = 9.0                 # reset button (K2) on the front edge, in line with D1; the owner: 9-10
USER_X = 49.0                 # user button (K1, D1: start/stop) on the front edge; the owner: 49-50
SIDE_Z = 1.8                  # centre height of the edge buttons and switch above EB's top
PLUNGER = 0.9                 # how far D1's and reset's plungers reach past EB's front edge (Seeed's footprint; measured ~0.9)
LEVER = 1.5                   # how far the switch's lever reaches past EB's back edge (15 Oct)
HOLES = [(3.99, 3.75), (53.99, 3.75), (3.99, 38.75), (53.99, 38.75)]  # mounting holes, 3 mm
USB_Z = SOCKET_H + 1.0 + 1.6  # USB-C centre above EB's top: sockets + XIAO PCB + half the receptacle
LOW_PARTS = [                 # tall parts under the lower level: (x0, y0, x1, y1, height, name)
    (24, 32.3, 34, 42.2, 6.0, "Grove"), (35.6, 32.3, 45.6, 42.2, 6.0, "Grove"),   # heights: usual parts
    (24, 0.4, 34, 10.3, 6.0, "Grove"), (35.6, 0.4, 45.6, 10.3, 6.0, "Grove"),
    (48.8, 6.9, 56.7, 15.0, 6.0, "battery socket"), (50.0, 16.9, 55.2, 27.1, 8.5, "pin headers"),
    (14.5, 0.3, 19.5, 5.3, 2.5, "buzzer"),
]
UNDER_PARTS = [(22.6, 14.4, 34.7, 28.0, 3.2, "coin cell, underneath"),
               (42.8, 13.8, 57.8, 28.8, 1.85, "microSD, underneath")]

# ---- on M1: the camera, the microSD card and the USB-C from Seeed's 3D model of the XIAO ESP32S3
# Sense (their wiki, read 15 Oct; the owner's side photo agrees within ~0.5 mm); the mic from their pinout
# drawing, CHECK. "Along" is from M1's USB-C edge (negative: past it), "across" from its front edge
# (the side with R; B faces the back), heights from M1's underside.
CAM_LENS = (3.53, XIAO_W / 2)  # the camera's lens: centred on its housing (Seeed: 0.6 off centre, side unknown)
CAM_BOX = (-0.47, 7.53, 8.0, 7.95, 13.71)   # its housing: along from, to; across; from, to in height. It rests on the card
CAM_OVER = -CAM_BOX[0]         # how far the housing overhangs M1's USB-C edge
CARD = (-3.11, 11.95, 11.18, 6.6, 8.25)     # the microSD card in the Sense board's slot, out over the USB-C (Seeed 3.1, photo ~2.6)
CARD_SIDE = 1.0                # the card sits 0.8 off M1's centre line in Seeed's model, to a side we can't tell: room both ways
USB_OVER = 1.53                # the USB-C receptacle past M1's edge (Seeed; photo ~1.2)
ANTENNA = (36.0, 18.0)        # the Wi-Fi antenna's foil (15 Oct); its U.FL cable is 55 mm
MIC_ON_M1 = (12.8, 1.9)       # the mic: a top-port MEMS part on the Sense board, near its front edge
MIC_PKG = (3.5, 2.65, 1.0)    # its package: the usual size (assumed)

# ---- design choices ---------------------------------------------------------------------------
WALL, TOP, PLATE = 2.0, 2.0, 2.0   # five perimeters with a 0.4 mm nozzle
GAP = 0.6                          # board edge to wall
FIT = 0.25                         # bottom plate to shell
R_PLAN, R_EDGE, CHAMFER = 4.5, 2.0, 0.6   # plan corners, top edges, bottom edge
BAY = 7.5                          # under EB: a flat LiPo up to 6 mm thick, plus air
CELL = (40.0, 30.0, 6.0)           # a 603040 flat LiPo (6 x 30 x 40 mm), JST-PH 2.0 (6 Oct)
LOW_CLEAR = 8.5 + 0.7              # pin headers + air
TALL_CLEAR = SOCKET_H + STACK_H + 0.7
WINDOW_MARGIN, WELL_WALL = 1.2, 1.2  # the open window (the alternative): margin round the active area
GLASS_T = 2.0                      # the deck: 2 mm grey-smoke acrylic, the same as the lid it replaces
LEDGE = 1.2                        # the ledge it rests on inside the walls, with a 45 degree chamfer under it
DECK_CLEAR = 0.3                   # glass to wall, each side: room for a hand-cut edge
CAM_HOLE = 7.0                     # the camera port, over the lens (6 Oct: for later)
SEATED = 34.0                      # a person across the table: eyes ~40 cm above it, ~60 cm away (assumed)

# ---- derived ----------------------------------------------------------------------------------
# M1's USB-C edge is flush with EB's (Seeed's layout), so the camera's overhang gets its own room at
# that end. The card reaches further: it goes into a slot in the end wall, above the USB-C (15 Oct).
END_ROOM = max(0.0, CAM_OVER - (XIAO_C[0] - XIAO_L / 2))
OX, OY = WALL + GAP + END_ROOM, WALL + GAP   # EB's front-left corner in case coordinates
L, W = EB_L + 2 * (WALL + GAP) + END_ROOM, EB_W + 2 * OY
Z_EB = PLATE + BAY                  # EB's underside
Z_EBT = Z_EB + EB_T                 # EB's top
H_LOW = Z_EBT + LOW_CLEAR + TOP
H_TALL = Z_EBT + TALL_CLEAR + TOP
X_STEP_IN = OX + XIAO_C[0] + XIAO_L / 2 + 0.8   # inside face of the step: clears M1
X_STEP = X_STEP_IN + WALL
Z_BTN = Z_EBT + SIDE_Z
M1_X0, M1_Y0 = XIAO_C[0] - XIAO_L / 2, XIAO_C[1] - XIAO_W / 2   # M1's USB-C edge and front edge, on EB
MIC_XY = (M1_X0 + MIC_ON_M1[0], M1_Y0 + MIC_ON_M1[1])
CAM_XY = (M1_X0 + CAM_LENS[0], M1_Y0 + CAM_LENS[1])
Z_M1 = Z_EBT + SOCKET_H            # M1's underside
SLOT_Y0 = OY + XIAO_C[1] - CARD[2] / 2 - CARD_SIDE   # the card slot's front edge
R_IN = R_PLAN - WALL                # the walls' inside corner radius
ANT_PATH = (X_STEP_IN - WALL - R_IN) + math.pi * R_IN / 2 + (SLOT_Y0 - WALL - R_IN)  # step to card slot, round the corner
assert ANT_PATH >= ANTENNA[0], "the antenna's foil doesn't fit round the tall end's corner"
Z_SENSE = Z_M1 + STACK_H - 4.5      # where the mic sits: CHECK (Seeed's model has the Sense board's top at Z_M1 + 5.18)


def eb(x: float, y: float) -> tuple[float, float]:
    return OX + x, OY + y


# ---- helpers ----------------------------------------------------------------------------------
def rrect(x0: float, y0: float, x1: float, y1: float, r: float) -> CrossSection:
    cs = CrossSection.square((x1 - x0 - 2 * r, y1 - y0 - 2 * r)).translate((x0 + r, y0 + r))
    return cs.offset(r, JoinType.Round, 2.0, SEG)


def box(x0, y0, z0, x1, y1, z1) -> Manifold:
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(h: float, r: float, axis: str = "z", r2: float | None = None) -> Manifold:
    c = Manifold.cylinder(h, r, r if r2 is None else r2, SEG)
    return {"z": c, "x": c.rotate((0, 90, 0)), "y": c.rotate((-90, 0, 0))}[axis]


def rounded_block(plan: CrossSection, h: float, r: float, chamfer: float, n: int = 10) -> Manifold:
    """A prism over a convex plan: top edges rounded with radius r, bottom edge chamfered."""
    eps = 0.01

    def at(cs: CrossSection, z: float) -> Manifold:
        return Manifold.extrude(cs, eps).translate((0, 0, z))

    slabs = [at(plan.offset(-chamfer, JoinType.Round, 2.0, SEG), 0.0), at(plan, chamfer)]
    for i in range(n + 1):
        t = math.pi / 2 * i / n
        inset = r - r * math.cos(t)
        cs = plan.offset(-inset, JoinType.Round, 2.0, SEG) if inset > 1e-6 else plan
        slabs.append(at(cs, h - r + r * math.sin(t) - eps))
    return Manifold.batch_hull(slabs)


def union(parts: list[Manifold]) -> Manifold:
    return Manifold.batch_boolean(parts, OpType.Add)


# ---- the shell --------------------------------------------------------------------------------
def slab(cs: CrossSection, z: float) -> Manifold:
    return Manifold.extrude(cs, 0.01).translate((0, 0, z))


def square(x0: float, y0: float, x1: float, y1: float) -> CrossSection:
    return CrossSection.square((x1 - x0, y1 - y0)).translate((x0, y0))


def deck_plan(clear: float = 0.0) -> CrossSection:
    """The lower level inside its walls: the glass deck's outline, all four corners R4, so it fits
    either way round."""
    r = R_PLAN - WALL
    return rrect(X_STEP + clear, WALL + clear, L - WALL - clear, W - WALL - clear, r - clear)


def window_rect() -> tuple[float, float, float, float]:
    """The open window at the glass: the active area plus a margin. Its left side is the step's
    face, so no thin strip of lid is left between them."""
    ax, ay = eb(*OLED_C)
    x0 = ax - OLED_AA[0] / 2 - WINDOW_MARGIN
    x0 = X_STEP if x0 - X_STEP < 2.0 else x0
    return x0, ay - OLED_AA[1] / 2 - WINDOW_MARGIN, ax + OLED_AA[0] / 2 + WINDOW_MARGIN, ay + OLED_AA[1] / 2 + WINDOW_MARGIN


def flare_slopes() -> tuple[float, float, float]:
    """How far the open window's front, back and right sides can slope out (run per mm of rise, at
    most 1, which is 45 degrees) before the well's wall hits a part beside the screen."""
    x0, y0, x1, y1 = window_rect()
    zb, zl, t = Z_EBT + OLED_TOP + 0.6, H_LOW - TOP, WELL_WALL

    def most(d: float, h: float) -> float:   # d: the part's distance out from the well's base
        zp = Z_EBT + h + 0.4
        return 1.0 if zp <= zb else max(0.0, min(1.0, d / (zp - zb)))

    boxes = [(*eb(a, b), *eb(c, d), h) for a, b, c, d, h, _ in LOW_PARTS]
    right = min([most(px0 - (x1 + t), h) for px0, py0, px1, py1, h in boxes
                 if px0 >= x1 and py1 > y0 - t and py0 < y1 + t] + [1.0])
    span = (x0 - t, x1 + t + right * (zl - zb))
    front = min([most((y0 - t) - py1, h) for px0, py0, px1, py1, h in boxes
                 if py1 <= y0 and px1 > span[0] and px0 < span[1]] + [1.0])
    back = min([most(py0 - (y1 + t), h) for px0, py0, px1, py1, h in boxes
                if py0 >= y1 and px1 > span[0] and px0 < span[1]] + [1.0])
    return front, back, right


def shell(top: str = "glass") -> Manifold:
    plan = rrect(0, 0, L, W, R_PLAN)
    outer = rounded_block(plan, H_LOW, R_EDGE, CHAMFER) + rounded_block(
        plan ^ CrossSection.square((X_STEP, W)), H_TALL, R_EDGE, CHAMFER)
    inner = rrect(WALL, WALL, L - WALL, W - WALL, R_PLAN - WALL)
    cavity = (Manifold.extrude(inner, H_LOW - TOP + 1).translate((0, 0, -1))
              + Manifold.extrude(inner ^ CrossSection.square((X_STEP_IN, W)), H_TALL - TOP + 1).translate((0, 0, -1)))
    s = outer - cavity

    if top == "glass":
        # The lower level's top is a pane of grey-smoke acrylic, flush with the walls: the octopus
        # glows through it from every seat at the table. It rests on a ledge inside the walls (a 45
        # degree chamfer under it, so it prints without support); the step's wall comes down to
        # carry the ledge on that side.
        P = deck_plan()
        Pi = P.offset(-LEDGE, JoinType.Round, 2.0, SEG)
        z1 = H_LOW - GLASS_T
        z0, zc = z1 - LEDGE, z1 - 2 * LEDGE
        ledge = Manifold.extrude(P, z1 - zc).translate((0, 0, zc)) - Manifold.batch_hull(
            [slab(P.offset(0.5, JoinType.Round, 2.0, SEG), zc - 0.5), slab(Pi, z0)]) \
            - Manifold.extrude(Pi, z1 - z0 + 1).translate((0, 0, z0))
        s = s + ledge + box(X_STEP_IN, WALL - 0.01, zc, X_STEP + 0.01, W - WALL + 0.01, H_LOW - TOP + 0.01)
        s = s - Manifold.extrude(P, GLASS_T + 1).translate((0, 0, z1))
    else:
        # The owner's sketch as drawn: an opening in the lid. The screen sits well below the lid (the
        # parts beside it are taller), so the opening is a well that comes down to 0.6 mm above
        # the glass and slopes out as far as the parts beside it allow, at most 45 degrees.
        x0, y0, x1, y1 = window_rect()
        sf, sb, sr = flare_slopes()
        zb, zl, t = Z_EBT + OLED_TOP + 0.6, H_LOW - TOP, WELL_WALL
        h, h2 = zl - zb, H_LOW + 0.5 - zb
        well = Manifold.batch_hull([slab(square(x0 - t, y0 - t, x1 + t, y1 + t), zb),
                                    slab(square(x0 - t, y0 - t - sf * h, x1 + t + sr * h, y1 + t + sb * h), zl + 0.01)])
        hole = Manifold.batch_hull([slab(square(x0, y0, x1, y1), zb - 0.7), slab(square(x0, y0, x1, y1), zb),
                                    slab(square(x0, y0 - sf * h2, x1 + sr * h2, y1 + sb * h2), H_LOW + 0.5)])
        s = s + well - hole

    # The microphone: one small port straight above it, and a faint ring outside so people can see
    # where it listens. The mic sits below the camera's height, so a short duct reaches down to 1 mm
    # above it; a foam ring there seals the port to the mic (unsealed, sound fills the box first).
    mx, my = eb(*MIC_XY)
    z_duct = Z_SENSE + MIC_PKG[2] + 1.0
    s = s + cyl(H_TALL - TOP - z_duct + 0.01, 2.0).translate((mx, my, z_duct))
    s = s - cyl(H_TALL - z_duct + 1, 0.8).translate((mx, my, z_duct - 0.5))
    s = s - (cyl(0.3, 2.9) - cyl(0.3, 2.3)).translate((mx, my, H_TALL - 0.3 + 0.001))

    # The camera stays, for later (6 Oct): a port over its lens, closed by a cap until then.
    kx, ky = eb(*CAM_XY)
    s = s - cyl(TOP + 2, CAM_HOLE / 2).translate((kx, ky, H_TALL - TOP - 1))
    s = s - cyl(0.401, CAM_HOLE / 2, "z", CAM_HOLE / 2 + 0.4).translate((kx, ky, H_TALL - 0.4))

    # Start/stop: a tongue cut in the front wall, with a nub on its back that presses D1. It's hinged
    # at its left end, so it bends along the print layers, not across them (where FDM parts crack),
    # and thinned there to 0.7 mm: about 1.3 N moves the nub 0.5 mm, with 1.3% strain at the hinge
    # (beam estimate, PETG at E ~2 GPa; D1's own spring adds to it). A shallow recess and a 0.6 mm shadow gap frame it; a raised dot
    # over the nub shows where to press.
    bx = OX + USER_X
    th, tl, tr = 3.5, bx - 10.0, bx + 2.0          # half height, hinge end, free end
    tongue = box(tl, -1, Z_BTN - th, tr, WALL + 1, Z_BTN + th)
    cut = box(tl, -1, Z_BTN - th - 0.6, tr + 0.6, WALL + 1, Z_BTN + th + 0.6) - tongue
    recess = Manifold.extrude(rrect(tl - 1.1, Z_BTN - th - 1.1, tr + 1.7, Z_BTN + th + 1.1, 2.2), 0.6) \
        .rotate((90, 0, 0)).translate((0, 0.6 - 0.001, 0))
    hinge = box(tl, 1.3, Z_BTN - th, tl + 1.5, WALL + 1, Z_BTN + th)
    s = s - recess - cut - hinge
    s = s + Manifold.sphere(0.8, SEG).scale((1, 0.5, 1)).translate((bx, 0.6, Z_BTN))

    # Reset: a pinhole in the front wall, for a paper clip, so it can't be pressed by accident.
    rx = OX + RESET_X
    s = s - cyl(WALL + 2, 0.8, "y").translate((rx, -1, Z_BTN))
    s = s - cyl(0.6, 1.6, "y", 0.8).translate((rx, -0.001, Z_BTN))
    # The plungers stick out 0.9 mm past EB's edge, 0.3 mm into the wall: a pocket behind each button
    # stops 0.2 mm short of its tip, and pins in EB's corner holes keep the board from sliding into them.
    for x in (OX + USER_X, rx):
        s = s - box(x - 2, WALL - (PLUNGER + 0.2 - GAP), Z_EBT - 0.3, x + 2, WALL + 0.1, Z_EBT + 3.0)

    # Battery switch: a slot over the lever, in a thumb-sized scallop.
    sx = OX + SWITCH_X
    s = s - box(sx - 3.5, W - WALL - 1, Z_BTN - 1.4, sx + 3.5, W + 1, Z_BTN + 1.4)
    s = s - Manifold.sphere(6.0, SEG).scale((1.7, 1, 1)).translate((sx, W + 5.2, Z_BTN))

    # USB-C: room for the plug's moulding, not just the socket.
    uy, uz = OY + XIAO_C[1], Z_EBT + USB_Z
    s = s - Manifold.extrude(rrect(uy - 6.25, uz - 3.5, uy + 6.25, uz + 3.5, 3.4), WALL + 2) \
        .rotate((90, 0, 0)).rotate((0, 0, 90)).translate((-1, 0, 0))
    # The microSD card runs out past M1's USB-C end to about the wall's outside face: a slot through the
    # end wall takes it, just above the USB-C opening and joined to it (15 Oct).
    _, _, w, h0, h1 = CARD
    s = s - box(-1, uy - w / 2 - CARD_SIDE, Z_M1 + h0 - 0.6, WALL + 0.1, uy + w / 2 + CARD_SIDE, Z_M1 + h1 + 0.6)

    # The antenna's foil is longer than any one wall of the tall end, so it goes round the inside corner
    # by the USB-C: along the front wall from the step, onto the end wall up to the card's slot, high up
    # and away from EB, in a 0.3 mm recess that holds it in place (15 Oct).
    s = s - antenna_band(0.0, 0.3, X_STEP_IN, SLOT_Y0)

    # Air: in low through the far end, up through the tall end, out its back (a chimney).
    for i in range(6):
        x = OX + 5.5 + i * 3.2
        s = s - box(x - 0.8, W - WALL - 1, H_TALL - TOP - 10, x + 0.8, W + 1, H_TALL - TOP - 2.5)
    for i in range(5):
        y = OY + 13 + i * 3.6
        s = s - box(L - WALL - 1, y - 0.8, Z_EBT + 1.5, L + 1, y + 0.8, Z_EBT + 6.5)

    # Bosses for the four screws that hold the bottom plate (from underneath, out of sight).
    for x, y in screw_points():
        s = s + cyl(5.0, 2.5).translate((x, y, PLATE)) - cyl(5.5, 0.9).translate((x, y, PLATE - 0.1))
    return s


def antenna_band(d0: float, d1: float, x1: float, y1: float) -> Manifold:
    """A band d0 to d1 into the tall end's front and USB-C end walls, just under its roof, up to x1 and y1."""
    inner = rrect(WALL, WALL, L - WALL, W - WALL, R_IN)
    ring = inner.offset(d1, JoinType.Round, 2.0, SEG) - inner.offset(d0, JoinType.Round, 2.0, SEG)
    z1 = H_TALL - TOP - 0.5
    return Manifold.extrude(ring, ANTENNA[1] + 0.6).translate((0, 0, z1 - ANTENNA[1] - 0.6)) ^ box(-1, -1, 0, x1, y1, H_TALL)


def camera_cap() -> Manifold:
    """The camera port's cap until the camera is used: flush on top, with a rim inside so it can't be
    pushed in. Fit it before EB goes in; take it out the same way."""
    x, y = eb(*CAM_XY)
    z = H_TALL - TOP
    return cyl(TOP, CAM_HOLE / 2 - 0.1).translate((x, y, z)) + cyl(0.5, CAM_HOLE / 2 + 0.5).translate((x, y, z - 0.5))


def screw_points() -> list[tuple[float, float]]:
    """M2 x 6 self-tapping screws through the plate into the shell, 4.6 mm in from the outside."""
    return [(x, y) for x in (L * 0.32, L * 0.68) for y in (WALL + 2.6, W - WALL - 2.6)]


# ---- the bottom plate -------------------------------------------------------------------------
def plate() -> Manifold:
    plan = rrect(WALL + FIT, WALL + FIT, L - WALL - FIT, W - WALL - FIT, R_PLAN - WALL - FIT)
    p = Manifold.extrude(plan, PLATE)
    for hx, hy in HOLES:                       # standoffs for EB's corner holes (M2 self-tapping), with a
        x, y = eb(hx, hy)                      # 2.7 mm pin up into the 3 mm hole that locates the board
        p = p + (cyl(BAY, 3.0) + cyl(BAY + 1.2, 1.35) - cyl(BAY + 1.2, 0.9)).translate((x, y, PLATE))
    cx, cy = OX + EB_L / 2, OY + EB_W / 2       # a low rim that keeps the cell in place
    cw, cd = CELL[0] / 2 + 0.5, CELL[1] / 2 + 0.5
    p = p + (box(cx - cw - 1, cy - cd - 1, PLATE, cx + cw + 1, cy + cd + 1, PLATE + 1.5)
             - box(cx - cw, cy - cd, PLATE - 0.1, cx + cw, cy + cd, PLATE + 2))
    for x, y in screw_points():                 # countersunk from below
        p = p - cyl(PLATE + 1, 1.2).translate((x, y, -0.5)) - cyl(1.0, 2.0, "z", 1.2).translate((x, y, -0.001))
    for x in (cx - 6, cx - 3, cx, cx + 3, cx + 6):   # intake slots in front of and behind the cell
        for y in (OY + 3.0, OY + EB_W - 3.0):
            p = p - box(x - 0.8, y - 1.6, -1, x + 0.8, y + 1.6, PLATE + 1)
    for x in (9.0, L - 9.0):                    # seats for four stick-on feet
        for y in (9.0, W - 9.0):
            p = p - cyl(0.6, 5.25).translate((x, y, -0.001))
    return p


# ---- the boards, for the x-ray view only ------------------------------------------------------
def boards() -> list[tuple[str, str, Manifold]]:
    out = []
    eb_body = Manifold.extrude(rrect(OX, OY, OX + EB_L, OY + EB_W, EB_R), EB_T).translate((0, 0, Z_EB))
    for hx, hy in HOLES:
        x, y = eb(hx, hy)
        eb_body = eb_body - cyl(EB_T + 1, 1.5).translate((x, y, Z_EB - 0.5))
    out.append(("EB, the expansion board", "pcb", eb_body))
    gx, gy = eb(*OLED_C)
    out.append(("OLED", "glass", box(gx - OLED_GLASS[0] / 2, gy - OLED_GLASS[1] / 2, Z_EBT, gx + OLED_GLASS[0] / 2, gy + OLED_GLASS[1] / 2, Z_EBT + OLED_TOP)))
    xx, xy = eb(*XIAO_C)
    sockets = union([box(xx - XIAO_L / 2 + 1, y - 1.3, Z_EBT, xx + XIAO_L / 2 - 1, y + 1.3, Z_EBT + 8.5) for y in (xy - 7.62, xy + 7.62)])
    out.append(("EB's sockets", "plastic", sockets))
    stack = Manifold.extrude(rrect(xx - XIAO_L / 2, xy - XIAO_W / 2, xx + XIAO_L / 2, xy + XIAO_W / 2, 1.2),
                             Z_SENSE - Z_M1).translate((0, 0, Z_M1))
    out.append(("M1, the XIAO ESP32S3 Sense", "m1", stack))
    ux, (kx, ky) = OX + M1_X0, eb(*CAM_XY)
    a0, a1, w, h0, h1 = CAM_BOX
    out.append(("camera", "m1", box(ux + a0, ky - w / 2, Z_M1 + h0, ux + a1, ky + w / 2, Z_M1 + h1)))
    out.append(("camera lens", "glass", cyl(0.1, 3.3).translate((kx, ky, Z_M1 + h1))))
    a0, a1, w, h0, h1 = CARD
    x1 = X_STEP_IN - 0.5                       # the foil, from 0.5 mm short of the step round to the end wall
    y1 = WALL + R_IN + ANTENNA[0] - (x1 - WALL - R_IN) - math.pi * R_IN / 2
    out.append(("antenna foil", "metal", antenna_band(0.1, 0.25, x1, y1)))
    out.append(("microSD card", "cell", box(ux + a0, xy - w / 2, Z_M1 + h0, ux + a1, xy + w / 2, Z_M1 + h1)))
    mx, my = eb(*MIC_XY)
    out.append(("microphone", "metal", box(mx - MIC_PKG[0] / 2, my - MIC_PKG[1] / 2, Z_SENSE, mx + MIC_PKG[0] / 2, my + MIC_PKG[1] / 2, Z_SENSE + MIC_PKG[2])))
    usb = box(xx - XIAO_L / 2 - USB_OVER, xy - 4.5, Z_EBT + USB_Z - 1.6, xx - XIAO_L / 2 + 6.8, xy + 4.5, Z_EBT + USB_Z + 1.6)
    out.append(("USB-C", "metal", usb))
    for x0, y0, x1, y1, h, name in LOW_PARTS:
        a, b = eb(x0, y0)
        c, d = eb(x1, y1)
        out.append((name, "plastic" if name != "pin headers" else "metal", box(a, b, Z_EBT, c, d, Z_EBT + h)))
    for x0, y0, x1, y1, h, name in UNDER_PARTS:
        a, b = eb(x0, y0)
        c, d = eb(x1, y1)
        out.append((name, "metal", box(a, b, Z_EB - h, c, d, Z_EB)))
    cx, cy = OX + EB_L / 2, OY + EB_W / 2
    out.append(("LiPo cell", "cell", box(cx - CELL[0] / 2, cy - CELL[1] / 2, PLATE, cx + CELL[0] / 2, cy + CELL[1] / 2, PLATE + CELL[2])))
    # the switch lever, the two buttons
    sx = OX + SWITCH_X
    out.append(("battery switch", "metal", box(sx - 3.5, OY + EB_W - 3.5, Z_EBT, sx + 3.5, OY + EB_W, Z_EBT + 3.4)
                + box(sx - 0.7, OY + EB_W, Z_BTN - 0.6, sx + 0.7, OY + EB_W + LEVER, Z_BTN + 0.6)))
    bx = OX + USER_X
    out.append(("D1 button", "plastic", box(bx - 3, OY, Z_EBT, bx + 3, OY + 3.5, Z_EBT + 3.4)
                + box(bx - 1.25, OY - PLUNGER, Z_EBT + 0.3, bx + 1.25, OY, Z_EBT + 1.5)))
    rx = OX + RESET_X
    out.append(("reset button", "plastic", box(rx - 3, OY, Z_EBT, rx + 3, OY + 3.5, Z_EBT + 3.4)
                + box(rx - 1.25, OY - PLUNGER, Z_EBT + 0.3, rx + 1.25, OY, Z_EBT + 1.5)))
    return out


def deck() -> Manifold:
    """The glass: what to cut, as a solid for the viewer."""
    return Manifold.extrude(deck_plan(DECK_CLEAR), GLASS_T).translate((0, 0, H_LOW - GLASS_T))


# ---- can you see the screen? ------------------------------------------------------------------
def section(ms: list[Manifold], x: float) -> list[np.ndarray]:
    """Cut the parts across at x (through the middle of the screen); polygons in (y, z)."""
    out = []
    for m in ms:
        out += [np.asarray(q, dtype=np.float64)[:, ::-1] for q in m.rotate((0, 90, 0)).slice(-x).to_polygons()]
    return out


def seen(polys: list[np.ndarray], deg: float, side: str = "front", n: int = 121) -> np.ndarray:
    """Which points across the screen's active area a person at `deg` above the table, facing the
    front (or the back), can see past the walls, the lid and the parts. True = seen."""
    _, ay = eb(*OLED_C)
    ys = np.linspace(ay - OLED_AA[1] / 2, ay + OLED_AA[1] / 2, n)
    o = np.stack([ys, np.full(n, Z_EBT + OLED_TOP + 0.01)], axis=1)
    t = math.radians(deg)
    d = np.array([-math.cos(t) if side == "front" else math.cos(t), math.sin(t)])
    e0 = np.concatenate(polys)
    e1 = np.concatenate([np.roll(q, -1, axis=0) for q in polys])
    e = e1 - e0
    cross = lambda a, b: a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]
    den = cross(d, e)                                    # (M,)
    w = e0[None, :, :] - o[:, None, :]                   # (N, M, 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        tt = cross(w, e[None]) / den
        uu = cross(w, np.broadcast_to(d, w.shape)) / den
    hit = (np.abs(den) > 1e-12) & (tt > 1e-6) & (uu >= 0) & (uu <= 1)
    return ~hit.any(axis=1)



def visibility(top: str, s: Manifold) -> dict:
    """The numbers for the page: how much of the screen a seated person sees, and from what angle
    they see all of it."""
    x = OX + OLED_C[0]
    blockers = section([s] + [m for n, k, m in boards() if k not in ("glass",)], x)
    full = lambda side: next((a / 2 for a in range(10, 180) if seen(blockers, a / 2, side).all()), 90.0)
    return {"front": float(seen(blockers, SEATED, "front").mean()), "back": float(seen(blockers, SEATED, "back").mean()),
            "full_front": full("front"), "full_back": full("back")}


def section_svg(top: str, s: Manifold) -> str:
    """A cut across the lower level through the screen, with a seated person's lines of sight."""
    x = OX + OLED_C[0]
    pad, H = 16.0, H_LOW + 9.0
    vb = (-pad, -2.5, W + 2 * pad, H + 4.0)
    fy = lambda z: H - z                                 # svg y grows down

    def path(polys: list[np.ndarray]) -> str:
        return " ".join("M" + " L".join(f"{y:.2f},{fy(z):.2f}" for y, z in q) + " Z" for q in polys)

    layers = [("sh", [s]), ("pl", [plate()])]
    kinds = {"pcb": "pcb", "glass": "oled", "plastic": "part", "metal": "part", "cell": "cell", "m1": "part"}
    for n, k, m in boards():
        layers.append((kinds.get(k, "part"), [m]))
    if top == "glass":
        layers.append(("deck", [deck()]))
    what = "dark glass" if top == "glass" else "an open window"
    out = [(f'<svg viewBox="{vb[0]:.1f} {vb[1]:.1f} {vb[2]:.1f} {vb[3]:.1f}" role="img" class="cut" '
            f'aria-label="A cut through the case with {what}, and the lines of sight of someone across the table">')]
    for cls, ms in layers:
        polys = section(ms, x)
        if polys:
            out.append(f'<path class="{cls}" fill-rule="evenodd" d="{path(polys)}"/>')
    for x0, y0, x1, y1, hh, name in LOW_PARTS:          # name what the cut passes through
        if name == "Grove" and OX + x0 < x < OX + x1:
            out.append(f'<text class="in" x="{OY + (y0 + y1) / 2:.2f}" y="{fy(Z_EBT + hh / 2) + 0.7:.2f}" text-anchor="middle">Grove</text>')
    out.append(f'<text class="in" x="{OY + EB_W / 2:.2f}" y="{fy(PLATE + CELL[2] / 2) + 0.7:.2f}" text-anchor="middle">battery</text>')
    out.append(f'<text x="{W - WALL - 1:.2f}" y="{fy(H_LOW) - 1.4:.2f}" text-anchor="end">{"glass" if top == "glass" else "lid"}</text>')
    blockers = section([s] + [m for n, k, m in boards() if k != "glass"], x)
    ok = seen(blockers, SEATED, "front", 6)
    _, ay = eb(*OLED_C)
    ys = np.linspace(ay - OLED_AA[1] / 2, ay + OLED_AA[1] / 2, 6)
    z0 = Z_EBT + OLED_TOP + 0.01
    t = math.radians(SEATED)
    for y, good in zip(ys, ok):
        if not good:   # draw the ray up to where it's blocked
            d = np.array([-math.cos(t), math.sin(t)])
            hits = []
            for q in blockers:
                for a, b in zip(q, np.roll(q, -1, axis=0)):
                    e = b - a
                    den = d[0] * e[1] - d[1] * e[0]
                    if abs(den) < 1e-12:
                        continue
                    w = a - np.array([y, z0])
                    tt = (w[0] * e[1] - w[1] * e[0]) / den
                    uu = (w[0] * d[1] - w[1] * d[0]) / den
                    if tt > 1e-6 and 0 <= uu <= 1:
                        hits.append(tt)
            k = min(hits)
            out.append(f'<line class="ray no" x1="{y:.2f}" y1="{fy(z0):.2f}" x2="{y - k * math.cos(t):.2f}" y2="{fy(z0 + k * math.sin(t)):.2f}"/>')
        else:
            k = (y + pad) / math.cos(t)
            out.append(f'<line class="ray" x1="{y:.2f}" y1="{fy(z0):.2f}" x2="{y - k * math.cos(t):.2f}" y2="{fy(z0 + k * math.sin(t)):.2f}"/>')
    ya, yb = ys[0], ys[-1]
    fine = seen(blockers, SEATED, "front", 121)
    yy = np.linspace(ya, yb, 121)
    run, start = [], None
    for y, g in zip(yy, fine):
        if g and start is None:
            start = y
        if not g and start is not None:
            run.append((start, y)); start = None
    if start is not None:
        run.append((start, yb))
    out.append(f'<line class="aa" x1="{ya:.2f}" y1="{fy(z0):.2f}" x2="{yb:.2f}" y2="{fy(z0):.2f}"/>')
    for a, b in run:
        out.append(f'<line class="aa lit" x1="{a:.2f}" y1="{fy(z0):.2f}" x2="{b:.2f}" y2="{fy(z0):.2f}"/>')
    out.append("</svg>")
    return "".join(out)


def deck_svg() -> str:
    """The glass outline at 1:1, for a laser shop or to print on paper as a cutting template."""
    polys = deck_plan(DECK_CLEAR).to_polygons()
    x0, y0, x1, y1 = deck_plan(DECK_CLEAR).bounds()
    w, h = x1 - x0, y1 - y0
    d = " ".join("M" + " L".join(f"{x - x0:.3f},{y1 - y:.3f}" for x, y in q) + " Z" for q in polys)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.2f}mm" height="{h:.2f}mm" viewBox="0 0 {w:.3f} {h:.3f}">'
            f'<path d="{d}" fill="none" stroke="#000" stroke-width="0.1"/></svg>\n')


# ---- output -----------------------------------------------------------------------------------
def write_stl(path: Path, m: Manifold) -> None:
    mesh = m.to_mesh()
    v = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    t = np.asarray(mesh.tri_verts, dtype=np.int64)
    tri = v[t]
    n = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    rec = np.zeros(len(t), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, tri
    with open(path, "wb") as f:
        f.write(b"octo case, concept v1".ljust(80, b" "))
        f.write(struct.pack("<I", len(t)))
        f.write(rec.tobytes())


def packed(m: Manifold) -> dict:
    mesh = m.calculate_normals(0, 32.0).to_mesh()
    vp = np.asarray(mesh.vert_properties, dtype=np.float32)
    tri = np.asarray(mesh.tri_verts, dtype=np.uint32)
    b64 = lambda a: base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()
    return {"pos": b64(vp[:, :3]), "nrm": b64(vp[:, 3:6]), "idx": b64(tri.reshape(-1)), "tris": len(tri)}


def main() -> None:
    here = Path(__file__).resolve().parent
    out = here / "out"
    out.mkdir(exist_ok=True)
    shells = {top: shell(top) for top in ("glass", "window")}   # the window only for the cut on the page
    s, p, g, c = shells["glass"], plate(), deck(), camera_cap()
    for part, name in ((s, "shell"), (p, "plate"), (c, "camera-cap")):
        assert part.status().name == "NoError", (name, part.status())
        write_stl(out / f"{name}.stl", part)
    # Nothing printed may touch a board, and the parts mustn't overlap each other.
    clashes = [f"{a} and {b}: {(m ^ n).volume():.2f} mm3"
               for a, m in (("case", s), ("plate", p), ("glass", g), ("camera cap", c))
               for b, k, n in boards() if (m ^ n).volume() > 1e-3]
    clashes += [f"{a} and {b}" for a, m, b, n in (("case", s, "plate", p), ("case", s, "glass", g), ("case", s, "camera cap", c))
                if (m ^ n).volume() > 1e-3]
    assert not clashes, "parts collide: " + "; ".join(clashes)
    (out / "deck.svg").write_text(deck_svg())
    ax, ay = eb(*OLED_C)
    mx, my = eb(*MIC_XY)
    gx0, gy0, gx1, gy1 = deck_plan(DECK_CLEAR).bounds()
    seen_by = {top: visibility(top, m) for top, m in shells.items()}
    model = {
        "size": [round(L, 1), round(W, 1), round(H_TALL, 1), round(H_LOW, 1)],
        "deck": {"size": [round(gx1 - gx0, 1), round(gy1 - gy0, 1), GLASS_T], "r": round(R_PLAN - WALL - DECK_CLEAR, 1)},
        "seated": SEATED,
        "seen": seen_by,
        "screen_depth": round(H_LOW - (Z_EBT + OLED_TOP), 1),
        "parts": [{"name": "case", "kind": "shell", **packed(s)}, {"name": "bottom plate", "kind": "plate", **packed(p)},
                  {"name": "glass deck", "kind": "deck", **packed(g)}, {"name": "camera cap", "kind": "cap", **packed(c)}]
        + [{"name": n, "kind": k, **packed(m)} for n, k, m in boards()],
        "screen": {"at": [ax, ay, Z_EBT + OLED_TOP + 0.03], "size": list(OLED_AA)},
        "labels": [
            {"id": "display", "at": [ax - 6, ay - 3, H_LOW], "n": [0, 0, 1]},
            {"id": "mic", "at": [mx, my, H_TALL], "n": [0, 0, 1]},
            {"id": "camera", "at": [*eb(*CAM_XY), H_TALL], "n": [0, 0, 1]},
            {"id": "record", "at": [OX + USER_X, 0.4, Z_BTN], "n": [0, -1, 0]},
            {"id": "reset", "at": [OX + RESET_X, 0.4, Z_BTN], "n": [0, -1, 0]},
            {"id": "power", "at": [OX + SWITCH_X, W, Z_BTN], "n": [0, 1, 0]},
            {"id": "usb", "at": [0, OY + XIAO_C[1], Z_EBT + USB_Z], "n": [-1, 0, 0]},
            {"id": "exhaust", "at": [OX + 13.5, W, H_TALL - TOP - 6], "n": [0, 1, 0]},
            {"id": "intake", "at": [L, OY + 20, Z_EBT + 4], "n": [1, 0, 0]},
            {"id": "floor", "at": [OX + EB_L / 2, OY + 3, 0], "n": [0, 0, -1]},
        ],
        "air": [
            [[L + 9, OY + 20, Z_EBT + 4], [L - 6, OY + 20, Z_EBT + 4.5], [X_STEP + 6, OY + 22, Z_EBT + 5.5],
             [X_STEP_IN - 6, OY + 25, H_TALL - TOP - 7], [OX + 11, W + 9, H_TALL - TOP - 6]],
            [[OX + 30, OY + 3, -6], [OX + 30, OY + 3, Z_EB - 1], [OX + 24, OY + 1.2, Z_EBT + 4],
             [X_STEP_IN - 5, OY + 14, H_TALL - TOP - 8], [OX + 8, W + 9, H_TALL - TOP - 6]],
        ],
    }
    (out / "model.json").write_text(json.dumps(model))
    # The viewer: one HTML file with the model, the two cuts and the recorder's screen code inlined.
    viewer = here / "viewer.html"
    if viewer.exists():
        oled = (here.parents[1] / "firmware" / "recorder" / "octo_display.js").read_text()
        oled = oled.replace("if (typeof module !== 'undefined') module.exports = OD;\n", "")
        sw, sg = seen_by["window"], seen_by["glass"]
        page = (viewer.read_text().replace("/*@MODEL@*/", json.dumps(model)).replace("/*@OLED@*/", oled)
                .replace("<!--@CUT_WINDOW@-->", section_svg("window", shells["window"]))
                .replace("<!--@CUT_GLASS@-->", section_svg("glass", shells["glass"])))
        for token, value in {"DEPTH": f"{model['screen_depth']:.1f}", "SEATED": f"{SEATED:.0f}",
                             "SEEN_W": f"{sw['front']:.0%}", "SEEN_WB": f"{sw['back']:.0%}",
                             "FULL_W": f"{sw['full_front']:.0f}", "FULL_G": f"{math.ceil(sg['full_front']):.0f}",
                             "DECK": f"{gx1 - gx0:.1f} × {gy1 - gy0:.1f} mm", "DECK_R": f"{R_PLAN - WALL - DECK_CLEAR:.1f}"}.items():
            page = page.replace(f"@@{token}@@", value)
        assert "@@" not in page, "a token in viewer.html wasn't filled"
        (out / "octo-case.html").write_text(page)
    print(f"case {L:.1f} x {W:.1f} mm, {H_TALL:.1f} mm tall at M1's end, {H_LOW:.1f} mm at the screen; "
          f"step at x = {X_STEP:.1f}; screen {H_LOW - Z_EBT - OLED_TOP:.1f} mm below the top")
    print(f"glass deck {gx1 - gx0:.1f} x {gy1 - gy0:.1f} x {GLASS_T} mm, corners R{R_PLAN - WALL - DECK_CLEAR:.1f}")
    print(f"open window slopes (front, back, right): {', '.join(f'{v:.2f}' for v in flare_slopes())}")
    for top, v in seen_by.items():
        print(f"{top:6}: at {SEATED:.0f} deg sees {v['front']:.0%} from the front, {v['back']:.0%} from the back; "
              f"all of it from {v['full_front']:.1f} deg (front), {v['full_back']:.1f} deg (back)")
    print(f"antenna foil {ANTENNA[0]:.0f} mm round the corner, in {ANT_PATH:.1f} mm of recess")
    print(f"triangles: shell {s.num_tri()}, plate {p.num_tri()}, camera cap {c.num_tri()}")


if __name__ == "__main__":
    main()
