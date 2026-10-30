"""Smallest gaps between the printed parts and each board part (nominal: sockets 11.0, M1 as modelled),
plus a USB-C plug at the spec's largest overmold (12.35 x 6.5) pushed fully home, and screw engagement."""
import sys
from manifold3d import Manifold
import boards as B
from check import load_stl, CASE, vol

shell = load_stl(CASE / "beta-top.stl", flip=True)
plate = load_stl(CASE / "beta-bottom.stl", flip=False)
rows = []
for sockets in (10.5, 11.0, 11.5):
    for mirror in (False, True):
        parts = B.eb_parts(13.0) + B.m1_parts(sockets, mirror) + \
            [("battery plug", B.battery_plug()), ("battery", B.cell())]
        for name, m in parts:
            if name == "EB":
                continue
            for pname, p in (("shell", shell), ("plate", plate)):
                g = p.min_gap(m, 3.0)
                rows.append((round(g, 2), pname, name, sockets, mirror))
best = {}
for g, pname, name, s, mir in rows:
    k = (pname, name)
    if k not in best or g < best[k][0]:
        best[k] = (g, s, mir)
print("closest approaches (worst over sockets 10.5-11.5 and both M1 orientations), under 1.0 mm:")
for (pname, name), (g, s, mir) in sorted(best.items(), key=lambda kv: kv[1][0]):
    if g < 1.0:
        print(f"  {g:5.2f} mm  {name} to the {pname}  (sockets {s}, {'mirrored' if mir else 'as modelled'})")

# a USB-C plug at the spec's largest overmold, fully home: its face on the receptacle's face
m1_edge = 10.54 - 10.5 - B.EBX
face = m1_edge - 1.53
for sockets in (10.5, 11.0, 11.5):
    for dy in (-0.25, 0.0, 0.25):
        zc = B.P["z_ebt"] + sockets + 2.1
        plug = Manifold.cube((20.0, 12.35, 6.5)).translate((face - 20.0, dy - 12.35 / 2, zc - 3.25))
        v = vol(shell, plug)
        print(f"USB-C plug (12.35 x 6.5) fully in, sockets {sockets}, off by {dy:+.2f}: "
              f"{'clear, gap %.2f' % shell.min_gap(plug, 3.0) if v < 1e-3 else 'BLOCKED %.2f mm3' % v}")

for name, m in B.screws():
    e = (shell ^ m).bounding_box()
    print(f"{name}: into the shell's pilot {e[5] - e[2]:.1f} mm" if (shell ^ m).volume() > 0 else f"{name}: no thread")
