# Fit check for `beta.scad`

Does the print fit the real boards? It reads the STL files you'd upload (`../beta-top.stl`,
`../beta-bottom.stl`) and puts Seeed's own boards inside them: EB from Seeed's PCB layout (v1.0), M1 and
the Sense board from Seeed's 3D model. Heights the layout doesn't give are the usual parts, marked EST in
`boards.py`.

```
cd hardware/case/fitcheck
python3 -m venv .venv-fit && .venv-fit/bin/pip install manifold3d trimesh cascadio numpy
./fetch.sh                                  # Seeed's files into seeed/ (gitignored), hash-checked
.venv-fit/bin/python check.py --offsets     # 60 configurations, ~20 s; exits 1 if anything doesn't fit
.venv-fit/bin/python margins.py             # closest gaps, a USB-C plug pushed fully in, screw depth
```

After changing `beta.scad`, render the STLs again first (`../README.md`, beta).

`check.py` tries every combination of these:
- M1's sockets at 10.5, 11.0 and 11.5 mm;
- M1 as modelled and mirrored;
- the switch on and off;
- with `--offsets`, EB 0.28 mm off the shell each way.

For each one it checks:
- No board part overlaps a printed part.
- The shell goes on from above, and EB goes onto the plate, without catching.
- The microSD card goes in through its slot.
- Each screw passes EB's hole and misses every part.

It does catch a bad case: on a copy with the far end 6 mm shorter and the grooves closed, it found the
plungers, the lever, the battery plug, M1 and EB catching on the way down.
