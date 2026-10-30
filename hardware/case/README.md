# The recorder's table case

Concept v1, 6 Oct 2026. The owner's sketch: two levels, with M1 (the XIAO ESP32S3 Sense) standing on EB
(the XIAO expansion board), so that end is taller, and EB's screen showing through the top of the
lower level. Here it is fitted to the boards, with the owner's decisions of 6 Oct. Since 15 Oct, positions on EB
come from Seeed's PCB layout (v1.0), checked against the owner's ruler. M1's camera, microSD card and USB-C come
from Seeed's 3D model of the Sense (the owner's side photo agrees within ~0.5 mm). M1's USB-C edge is flush
with EB's, so the camera (0.47 mm past it) gets 0.4 mm of room at that end, and the card (3.1 mm past)
goes into a slot through the end wall, joined to the top of the USB-C opening, its tip about flush with
the outside (15 Oct). **Don't print it yet:** the mic's position and the sockets' height are
still CHECK.

The design review (3D model, the decisions, what's estimated) is published privately at
https://claude.ai/artifact/34PcDpmUzvn1zrZv1ExBsR. To republish it from a new session, pass that URL
as `url`. In the lab it's Setup → Recorder case (`/case`): `manifold3d` is one of the lab's
dependencies (since 6 Oct), so the page is rebuilt there whenever `case.py`, `viewer.html` or
`firmware/recorder/octo_display.js` change; without it, the link opens the published copy.

## `beta.scad`: the one to print (15 Oct)

An OpenSCAD version of the same case for a print service, with tongues for D1 and reset and a slot
for the switch. Checked against Seeed's boards with `fitcheck/` (README there): it fits in all 60
combinations of M1's socket height (10.5–11.5 mm), M1's two possible orientations, the switch on and off,
and EB 0.28 mm off the shell each way.
- Size: 72 × 48.4 mm; 43.6 mm tall at M1, 26.1 at the screen. `case.py`'s case is 63.6 mm long; here the
  far end has 8 mm more, for the battery plug and its wires.
- The 603040 sits on the plate, under EB.
- The screen's window takes a 24.6 × 17.3 mm pane of 1 mm smoke acrylic.

```
O=/Applications/OpenSCAD-2021.01.app/Contents/MacOS/OpenSCAD
$O -o beta-top.stl    -D 'part_to_render="top"'      beta.scad   # the shell, upside down
$O -o beta-bottom.stl -D 'part_to_render="bottom"'   beta.scad   # the plate
$O -o beta-test-fit.stl -D 'part_to_render="test_fit"' beta.scad # a ring of the shell at button height
```

How it goes together:
1. EB goes onto the plate's four standoffs. Pins on two of them go up through EB's corner holes.
2. The battery plugs into J2, which faces the far end.
3. The shell goes on from above. The pins go into sockets in its posts, and grooves inside its walls let
   the plungers, the lever and the USB-C slide past.
4. Four M2 × 20 countersunk screws go in from underneath: two through EB's other holes into the shell's
   posts (5.5 mm of thread), two into the far corners' columns (7.5 mm).
5. The microSD card goes in last, through its slot.

Before ordering:
- Measure EB's top to M1's underside (the sockets). The check covers 10.5–11.5 mm.

Smallest gaps (`margins.py`):
- The card's sides in its slot: 0.48 mm.
- The Sense board to the step: 0.52 mm.
- The plungers to their pockets: 0.6 mm.
- A USB-C plug at the spec's largest moulding (12.35 × 6.5 mm), pushed fully in: 0.27 mm.

## Build

```
pip install manifold3d numpy
python3 hardware/case/case.py
```

It writes `out/` (gitignored):

| File | What |
|---|---|
| `shell.stl` | The case, with the ledge for the glass deck |
| `plate.stl` | The bottom plate. EB screws to it, and it screws to the case from underneath |
| `camera-cap.stl` | The cap that closes the camera port until the camera is used |
| `deck.svg` | The glass outline at 1:1, for a laser-cutting shop or as a paper template |
| `octo-case.html`, `model.json` | The review page: `viewer.html` with the model, two cuts through the screen and the recorder's screen code (`firmware/recorder/octo_display.js`) inlined |

The build fails if any printed part touches a board or another printed part. It also prints how
much of the screen someone across the table sees (through the glass, and through the sketch's open
window for comparison).

## The owner's brief and decisions (6 Oct)

Brief:
- Plungers for the side buttons.
- The boards' own lights.
- EB's on/off switch and reset button reachable, as well as start/stop.
- Minimal and usable, with no bulk.
- Room for heat.

Decided:
- **Top: dark glass.** The screen sits 9.2 mm below the top, because the pin headers and Grove
  sockets beside it are taller. Through an opening, someone across the table (34°) would see 52% of
  it; through a flush pane of 2 mm grey-smoke acrylic they see all of it.
- **Recording light: the screen** (15 Oct; was the D0 LED). No LED is added. The screen is always
  on, dim, and shows REC and the time while recording, under the glass. The boards' own lights stay:
  M1's charge light should show through the USB-C opening (not checked); EB's power light is under the lid.
- **Battery: a 603040 flat LiPo** (6 × 30 × 40 mm) with a JST-PH 2.0 plug, so the switch is a real off.
- **Camera: kept**, for a later enhancement, with a 7 mm port over its lens and a cap until then. As
  M1 stands, the lens looks straight up.
- **Pin headers: left** as they are.

## The rest of the design

- **Buttons and the board:** D1's and reset's plungers reach 0.9 mm past EB's front edge (Seeed's
  footprint; the owner agrees), 0.3 mm more than the 0.6 mm gap. A pocket behind each tongue stops 0.2 mm
  short of the plunger, and 2.7 mm pins on the plate's standoffs (here and in `beta.scad`) in
  EB's 3 mm corner holes keep the board from sliding into them. The switch's lever reaches 1.5 mm into
  its slot.
- **Start/stop:** a tongue in the front wall presses D1. It's hinged at its side, so it
  bends along the print layers, and the hinge is thinned to 0.7 mm. A beam estimate gives about 1 N on
  top of D1's own spring.
- **Reset:** a pinhole for a paper clip, in the front wall beside start/stop (reset is on EB's front edge).
- **Switch:** a slot with a thumb scallop.
- **USB-C:** an opening at M1's end, sized for the plug's moulding.
- **Mic:** a 1.6 mm port straight above it. The mic sits on the Sense board, below the camera's
  height, so a short duct reaches down to it; a foam ring seals the duct to the mic.
- **Air:** in at the far end and through the floor, out of six slots high on the back of the tall end.
  The battery sits lowest, in the coolest air.
- **Antenna:** its foil (36 × 18 mm, a 55 mm U.FL cable) is longer than any one wall of the tall
  end, so it goes round the inside corner by the USB-C: along the front wall from the step, onto the
  end wall up to the card's slot, just under the roof, in a 0.3 mm recess that locates it (1.1 mm to
  spare here, 0.6 in `beta.scad`). Its own adhesive holds it. Check the Wi-Fi signal before and after.
- **Material:** PETG, which softens around 80 °C (PLA around 60 °C).
- Three printed parts, one acrylic pane, 8 screws (M2 self-tapping) and 4 stick-on feet.

`case.py` says where every number comes from. `B` (boot) on M1 can't be reached from outside; it's
only needed to recover a bad USB flash, so open the case for it.

## Before printing: measure

There are no calipers. EB's outline (58 × 42.5 mm) is published, so it calibrates photos. Take three
photos with a ruler in each:

1. **From above:** boards flat on white paper, phone parallel to the table about 30 cm up, at 2× zoom.
   M1's mic and camera show in this one.
2. **From the side:** at table height, with the ruler standing upright beside the stack. It gives the
   camera's height above the Sense board.
3. **From underneath:** for the microSD slot and the coin-cell holder.

Two checks: Seeed's drawing puts the mic near M1's front edge (the side with R), about 13 mm from the
USB-C end; is the small square with a pinhole there? Then update the
values marked CHECK in `case.py` and run it again.

## Print, cut and assemble

1. The bottom plate, about an hour: checks the holes and the battery bay.
2. A 12 mm ring of the case at button height: in the slicer, cut the case at 7 and 19 mm and print
   the middle. It checks how start/stop feels, and that the pinhole and the switch slot line up.
3. The case, standing upright, with tree supports inside, and the camera cap with it.
4. The glass: 34.2 × 43.1 mm from 2 mm grey-smoke acrylic, corners R2.2. Score and snap it, then file
   the corners, or have a laser shop cut `out/deck.svg`. Check a light and a dark sample over the
   running screen first. Fix it with clear silicone or thin clear tape, not superglue, which fogs
   acrylic white.
5. Inside:
   - The cap goes into its port from inside, before EB goes in.
   - A foam ring, about 4 mm across with a 1.5 mm hole, goes on the end of the mic's duct.
   - EB's corner holes go over the four pins on the plate's standoffs.
   - Check the battery's polarity with a multimeter before plugging it in.
6. Then measure:
   - **The mic:** the same 30 seconds at 1 m, with and without the case (`bench analyse`).
   - **Heat:** the ESP32-S3's own sensor (a small firmware change to report it in `/status`).
   - **The screen:** through the glass it looks dimmer; check the dim always-on screen still reads
     from across the table.
