"""The real boards in beta.scad's frame (origin: EB's centre in plan, z = 0 on the table).

EB: positions from Seeed's layout (Seeeduino XIAO Expansion board_v1.0_200824.brd); heights from the
package names where they give one, Seeed's product photo, or the usual part (marked EST).
M1 + Sense: Seeed's 3D model (seeed-studio-xiao-esp32s3-sense-3d_model.zip), every solid as modelled.
"""
from __future__ import annotations

import re
import math
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import CrossSection, Manifold, Mesh

HERE = Path(__file__).resolve().parent
BETA = HERE.parent / "beta.scad"
GLB = HERE / "seeed" / "sense.glb"     # ./fetch.sh


def scad_params() -> dict:
    src = BETA.read_text()
    out = {}
    for k in ("wall", "gap", "eb_t", "under_eb_h", "ext"):
        out[k] = float(re.search(rf"\b{k}\s*=\s*([\d.]+);", src).group(1))
    out["cell_h"] = float(re.search(r"^cell\s*=\s*\[[\d.]+,\s*[\d.]+,\s*([\d.]+)\]", src, re.M).group(1))
    out["z_eb"] = out["wall"] + out["cell_h"] + out["under_eb_h"] + 0.5
    out["z_ebt"] = out["z_eb"] + out["eb_t"]
    return out


P = scad_params()
EBX, EBY = 29.0, 21.25          # EB's centre in EB coordinates
SEG = 48


def box(x0, y0, z0, x1, y1, z1) -> Manifold:
    return Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def top(x0, x1, y0, y1, h, z0=0.0) -> Manifold:
    """A part on EB's top, in EB coordinates, from z0 to h above EB's top."""
    zt = P["z_ebt"]
    return box(x0 - EBX, y0 - EBY, zt + z0, x1 - EBX, y1 - EBY, zt + h)


def under(x0, x1, y0, y1, h) -> Manifold:
    zb = P["z_eb"]
    return box(x0 - EBX, y0 - EBY, zb - h, x1 - EBX, y1 - EBY, zb)


def cyl(h, r, x, y, z) -> Manifold:
    return Manifold.cylinder(h, r, r, SEG).translate((x, y, z))


def eb_plate() -> Manifold:
    r = 3.0                                    # the layout's corner arcs
    pts = []
    for cx, cy, a0 in ((58 - r, 42.5 - r, 0), (r, 42.5 - r, 90), (r, r, 180), (58 - r, r, 270)):
        for i in range(13):
            a = math.radians(a0 + 90 * i / 12)
            pts.append((cx + r * math.cos(a) - EBX, cy + r * math.sin(a) - EBY))
    plate = Manifold.extrude(CrossSection([pts]), P["eb_t"]).translate((0, 0, P["z_eb"]))
    for hx, hy in HOLES:
        plate = plate - cyl(P["eb_t"] + 2, 1.5, hx - EBX, hy - EBY, P["z_eb"] - 1)
    return plate


HOLES = [(3.99, 3.75), (53.99, 3.75), (3.99, 38.75), (53.99, 38.75)]   # milled, r 1.5 (layer 20)
TAIL = 2.5   # THT tails below EB: EST (the owner's photo shows ~1.5 under the switch)


BRD = HERE / "seeed" / "eb.brd"
# heights by package (mm above the board): the usual parts; EST unless the package name gives it
PKG_H = [("0402", 0.6), ("0603", 0.9), ("L0806", 1.2), ("L201610", 1.0), ("SOD-523", 0.7), ("SOD-323", 1.1),
         ("SOT-323", 1.1), ("SOT-23", 1.45), ("QFN", 0.9), ("DFN", 0.9), ("UDFN", 0.6), ("SOP8", 1.75),
         ("X2-SMD", 0.55), ("TP-", 0.05), ("3P-SMD-SHORT", 0.05), ("BAT2P", 3.2), ("MICRO-SD", 1.85)]
# parts modelled by hand below, with what the package name, Seeed's photo or the owner's ruler says
BY_HAND = {"U2", "U6", "J1", "J4", "J5", "J6", "J2", "J7", "J10", "J11", "BUZ1", "SW1", "K1", "K2"}


def brd_elements():
    import xml.etree.ElementTree as ET
    brd = ET.parse(BRD).getroot().find(".//board")
    pk = {(l.get("name"), p.get("name")): p for l in brd.findall("./libraries/library")
          for p in l.findall("./packages/package")}
    for e in brd.findall("./elements/element"):
        p = pk[(e.get("library"), e.get("package"))]
        rot = e.get("rot", "R0")
        mir = rot.startswith("M")
        a = math.radians(float(rot.lstrip("M").lstrip("R") or 0))
        x0, y0 = float(e.get("x")), float(e.get("y"))
        pts = []
        def tf(x, y):
            x = -x if mir else x
            return x0 + x * math.cos(a) - y * math.sin(a), y0 + x * math.sin(a) + y * math.cos(a)
        for w in p.findall("wire"):
            if w.get("layer") in ("21", "22", "51", "52"):
                pts += [tf(float(w.get("x1")), float(w.get("y1"))), tf(float(w.get("x2")), float(w.get("y2")))]
        for t in ("smd", "pad"):
            for q in p.findall(t):
                cx, cy = float(q.get("x")), float(q.get("y"))
                dx = float(q.get("dx", q.get("diameter", q.get("drill", "1")))) / 2
                dy = float(q.get("dy", q.get("diameter", q.get("drill", "1")))) / 2
                pts += [tf(cx - dx, cy - dy), tf(cx + dx, cy + dy), tf(cx - dx, cy + dy), tf(cx + dx, cy - dy)]
        if pts:
            xs, ys = [q[0] for q in pts], [q[1] for q in pts]
            yield e.get("name"), e.get("package"), mir, (min(xs), max(xs), min(ys), max(ys))


def eb_parts(lever_x: float) -> list[tuple[str, Manifold]]:
    """EB and everything on it. lever_x: the switch lever's centre (the owner: 13 on, 15.5 off)."""
    parts = [("EB", eb_plate())]
    for name, pkg, bottom, (x0, x1, y0, y1) in brd_elements():
        if name in BY_HAND:
            continue
        h = next((hh for key, hh in PKG_H if key in pkg), 1.5)
        x0, y0 = max(x0, 0.0), max(y0, 0.0)
        parts.append((f"{name} ({pkg})", under(x0, x1, y0, y1, h) if bottom else top(x0, x1, y0, y1, h)))
    T = [
        ("OLED (U6)", 22.43, 47.17, 12.80, 29.70, 2.0),            # EST thickness
        ("Grove J1", 35.6, 45.6, 32.3, 42.2, 6.0), ("Grove J4", 24.0, 34.0, 32.3, 42.2, 6.0),
        ("Grove J5", 24.0, 34.0, 0.3, 10.3, 6.0), ("Grove J6", 35.6, 45.6, 0.3, 10.3, 6.0),  # EST (photo ~5)
        ("battery socket J2", 48.65, 56.65, 6.87, 15.0, 5.5),          # S2B-PH-SM4-TB: 5.5 tall (distributors)
        ("2x4 pin header J7", 49.78, 55.37, 16.89, 27.05, 8.5),        # 2.54 header: 2.5 + 6
        ("female header J10", 1.65, 19.43, 9.53, 12.07, 8.5), ("female header J11", 1.65, 19.43, 30.53, 33.07, 8.5),
        ("buzzer", 14.52, 19.52, 0.26, 5.26, 2.5),                      # BZ3-SMD-5.0X5.0X2.5MM
        ("switch body SW1", 10.44, 19.54, 37.37, 40.87, 3.2),          # SW6-...-9.0X3.5X3.2MM
        ("D1 button body K1", 46.67, 51.37, 0.35, 2.60, 1.8), ("reset button body K2", 6.66, 11.36, 0.35, 2.60, 1.8),
    ]
    for name, x0, x1, y0, y1, h in T:
        parts.append((name, top(x0, x1, y0, y1, h)))
    # the plungers: 2.0 wide, out to 0.9 past the front edge (package), at button height (EST 0.3-1.5)
    for name, x in (("D1 plunger", 49.022), ("reset plunger", 9.015)):
        parts.append((name, top(x - 1.0, x + 1.0, -0.903, 0.35, 1.5, 0.3)))
    # the lever: 1.5 wide, to 44.07 (1.57 past the back edge), EST 0.6-2.6 high
    parts.append(("switch lever", top(lever_x - 0.75, lever_x + 0.75, 40.87, 44.07, 2.6, 0.6)))
    tails = [(x, y) for y in (13.63, 28.87, 10.8, 31.8) for x in (2.92, 5.46, 8.0, 10.54, 13.08, 15.62, 18.16)]
    tails += [(x, y) for y in (33.8, 8.8) for x in (26.0, 28.0, 30.0, 32.0, 37.6, 39.6, 41.6, 43.6)]
    tails += [(x, y) for y in (37.47, 40.77) for x in (12.45, 14.99, 17.53)]
    for x, y in tails:
        parts.append((f"pin tail under EB at ({x}, {y})", cyl(TAIL, 0.5, x - EBX, y - EBY, P["z_eb"] - TAIL)))
    return parts


def battery_plug() -> Manifold:
    """A JST-PH plug (PHR-2, 5.8 wide) in J2, which faces EB's far end (Seeed's diagram): it runs ~4.5 past the
    socket's mouth (~57.3), and its wires need ~2.5 more to turn down to the cell. EST."""
    return top(55.0, 57.3 + 4.5 + 2.5, 10.94 - 2.9, 10.94 + 2.9, 5.0, 0.3)


def cell(dims=(40.0, 30.0, 6.0)) -> Manifold:
    """The battery on the plate, centred under EB (603040, the owner's 6 Oct plan)."""
    l, w, h = dims
    return box(-l / 2, -w / 2, P["wall"], l / 2, w / 2, P["wall"] + h)


def screws() -> list[tuple[str, Manifold]]:
    """M2 x 20 countersunk from underneath: up through EB's holes 1 and 4, and into the far end's columns."""
    xs = [(3.99 - EBX, 3.75 - EBY), (53.99 - EBX, 38.75 - EBY)]
    xf = 29.0 + P["gap"] + P["ext"] - 2.6
    yf = 21.25 + P["gap"] - 2.6
    xs += [(xf, -yf), (xf, yf)]
    return [(f"screw at ({x:.1f}, {y:.1f})", cyl(19.0, 1.0, x, y, 1.0)) for x, y in xs]


def m1_parts(sockets: float, mirror: bool) -> list[tuple[str, Manifold]]:
    """Seeed's 3D model of M1, placed on EB: USB-C at EB's x 0 end, M1's centre at (10.54, 21.25)."""
    sc = trimesh.load(GLB)
    xc, zc = 1.805, -6.11                      # the main board's centre in the model (mm)
    out = []
    zt = P["z_ebt"]
    for name in sc.graph.nodes_geometry:
        T, g = sc.graph[name]
        m = sc.geometry[g].copy()
        m.apply_transform(T)
        v = m.vertices * 1000.0
        X, Y, Z = v[:, 0], v[:, 1], v[:, 2]
        x = (10.54 - EBX) - (X - xc)
        y = (Z - zc) * (-1 if mirror else 1)
        z = zt + sockets + Y
        verts = np.stack([x, y, z], axis=1).astype(np.float32)
        faces = m.faces.astype(np.uint32)
        if mirror:
            faces = faces[:, ::-1].copy()
        man = Manifold(Mesh(vert_properties=verts, tri_verts=faces))
        if man.status().name != "NoError" or man.volume() <= 0:
            man = Manifold.hull_points(verts)       # not a closed solid in the model: its hull, conservatively
        label = re.sub(r"_[0-9a-f]{6}$", "", name)
        out.append((f"M1: {label}", man))
    # the headers between EB's sockets and M1: two rows, 2.54 wide, the length of the female headers
    for y in (13.63, 28.87):
        out.append(("M1's headers", top(1.65, 19.43, y - 1.27, y + 1.27, sockets)))
    return out
