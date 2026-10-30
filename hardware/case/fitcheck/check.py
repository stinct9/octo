"""Does beta.scad's print fit the real boards? Reads the STL files that would be uploaded.

1. In place: nothing printed overlaps a board part; the smallest gap to each.
2. Assembly: the shell goes on from above (equivalently, the boards go in from below), so every board
   part sweeps down through the shell; EB goes onto the plate from above, so parts sweep up through it.
3. The microSD card goes in through its slot afterwards, along -x.
4. The screws: each passes EB's hole and misses every board part.
Each over: M1 as Seeed's model has it and mirrored across (the model doesn't say which side is which),
M1's sockets at 10.5, 11.0 and 11.5 mm (EB's top to M1's underside: 11.0 from the photo, +-0.5), the
switch lever on and off; with --offsets, EB 0.28 mm off the shell each way too (the pins' play, both ends).

    python check.py [--offsets] [--case DIR]     # DIR: where the STLs are (default: hardware/case)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import trimesh
from manifold3d import Manifold, Mesh

import boards as B

CASE = Path(sys.argv[sys.argv.index("--case") + 1]) if "--case" in sys.argv else Path(__file__).resolve().parent.parent


def load_stl(path: Path, flip: bool) -> Manifold:
    m = trimesh.load(path, force="mesh")
    v = m.vertices.copy()
    f = m.faces.copy()
    if flip:                       # the top is printed upside down: rotate([180, 0, 0])
        v[:, 1] *= -1
        v[:, 2] *= -1
    man = Manifold(Mesh(vert_properties=v.astype(np.float32), tri_verts=f.astype(np.uint32)))
    assert man.status().name == "NoError", (path.name, man.status())
    return man


def sweep(part: Manifold, down: bool, to: float) -> Manifold:
    """The part and everywhere it passed through, moving straight down (or up) to z = to."""
    lo, hi = part.bounding_box()[2], part.bounding_box()[5]
    cs = part.project()
    if down:
        return Manifold.extrude(cs, hi - to).translate((0, 0, to))
    return Manifold.extrude(cs, to - lo).translate((0, 0, lo))


def vol(a: Manifold, b: Manifold) -> float:
    return (a ^ b).volume()


def where(m: Manifold) -> str:
    b = m.bounding_box()
    return f"x {b[0]:.1f}..{b[3]:.1f} y {b[1]:.1f}..{b[4]:.1f} z {b[2]:.1f}..{b[5]:.1f}"


def main() -> int:
    shell = load_stl(CASE / "beta-top.stl", flip=True)
    plate = load_stl(CASE / "beta-bottom.stl", flip=False)
    print("shell", where(shell), f"volume {shell.volume():.0f} mm3, genus {shell.genus()}")
    print("plate", where(plate), f"volume {plate.volume():.0f} mm3, genus {plate.genus()}")
    print(f"EB underside z {B.P['z_eb']:.2f}, top z {B.P['z_ebt']:.2f}")
    problems = 0
    shell0 = shell
    # EB may sit 0.15 off the plate's pins and the shell 0.15 off them the other way: 0.3 between them
    offsets = [(0, 0)] + ([(1, 0), (-1, 0), (0, 1), (0, -1)] if "--offsets" in sys.argv else [])
    for (ox, oy) in offsets:
      shell = shell0.translate((-0.14 * ox, -0.14 * oy, 0))
      for sockets in (10.5, 11.0, 11.5):
        for mirror in (False, True):
            for lever in (13.0, 15.5):
                tag = f"[sockets {sockets}, M1 {'mirrored' if mirror else 'as modelled'}, lever at {lever}, EB off the shell by ({0.28 * ox:+.2f}, {0.28 * oy:+.2f})]"
                parts = B.eb_parts(lever) + B.m1_parts(sockets, mirror) + [("battery plug", B.battery_plug()), ("battery", B.cell())]
                parts = [(n, m.translate((0.14 * ox, 0.14 * oy, 0))) for n, m in parts]
                card = [(n, m) for n, m in parts if "MicroSD v2" in n]
                rest = [(n, m) for n, m in parts if "MicroSD v2" not in n]
                lines = []
                for name, m in parts:
                    v1, v2 = vol(shell, m), vol(plate, m)
                    if v1 > 1e-3 or v2 > 1e-3:
                        lines.append(f"  IN PLACE: {name} overlaps the {'shell' if v1 > 1e-3 else 'plate'} "
                                     f"({max(v1, v2):.2f} mm3) at {where(shell ^ m if v1 > 1e-3 else plate ^ m)}")
                for name, m in rest:
                    s = sweep(m, down=True, to=-1.0)
                    v = vol(shell, s)
                    if v > 1e-3:
                        lines.append(f"  SHELL ON: {name} runs into the shell ({v:.2f} mm3) at {where(shell ^ s)}")
                    s = sweep(m, down=False, to=200.0)
                    v = vol(plate, s)
                    if v > 1e-3:
                        lines.append(f"  ONTO PLATE: {name} runs into the plate ({v:.2f} mm3) at {where(plate ^ s)}")
                for name, m in card:
                    out = Manifold.batch_hull([m, m.translate((-30.0, 0, 0))])
                    v = vol(shell, out)
                    if v > 1e-3:
                        lines.append(f"  CARD IN: the card hits the shell going in ({v:.2f} mm3) at {where(shell ^ out)}")
                # the plate: standoffs, pins and ribs are upright prisms, so in place is the whole check
                pv = vol(shell, plate)
                if pv > 1e-3:
                    lines.append(f"  IN PLACE: the plate overlaps the shell ({pv:.2f} mm3) at {where(shell ^ plate)}")
                for name, m in ([] if (ox or oy) else B.screws()):
                    for bname, bm in rest:
                        if vol(m, bm) > 1e-3 and bname != "EB":
                            lines.append(f"  SCREW: {name} hits {bname}")
                    if vol(m, B.eb_plate()) > 1e-3:  # nominal only
                        lines.append(f"  SCREW: {name} doesn't pass EB's hole")
                if lines:
                    problems += len(lines)
                    print(tag)
                    print("\n".join(dict.fromkeys(lines)))
                else:
                    print(tag, "fits")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
