// ============================================================================
// OCTO CONVERSATION LAB - SIDE-WALL BETA ENCLOSURE, fitted to hardware/case/case.py
// Controls (each presses the board's own part from the side, as in case.py):
//   1. Front wall (-Y): EB D1 user button -> 12mm flex tongue with a concave dish
//   2. Back wall  (+Y): EB power switch   -> recessed slide-switch slot
//   3. Front wall (-Y): EB reset button   -> 7mm flex tongue, slightly recessed, in line with D1
// The recording light is the screen (REC and the time while recording, never dark): no added LED.
//   M1's boot button can't be reached from outside (case.py README): open the case for it.
// Origin: EB's centre in plan, z = 0 on the table. M1 sits at the -X end.
//
// How it goes together (checked against Seeed's layout and 3D model, 15 Oct; hardware/case/fitcheck/):
//   1. EB onto the plate: it sits on four standoffs under its corner holes. Pins on two of them
//      (diagonal) go up through EB's holes and locate it.
//   2. The battery plugs into EB's socket, which faces EB's far end: the plug and the turn of its
//      wires take the far end's extra 8mm.
//   3. The shell goes on from above. The pins' tips go into sockets in its posts, so it lines up with
//      EB; grooves inside its walls let the parts that stick out past EB's edges (D1 and reset's
//      plungers, the switch lever, the USB-C socket) slide past on the way down.
//   4. Four M2 x 20 countersunk screws from underneath: two up through EB's other holes into the
//      shell's posts, two into columns in the far end's corners.
//   5. The microSD card goes in last, through its slot in the end wall.
// Board numbers in EB coordinates (from EB's front-left corner): positions on EB from Seeed's PCB
// layout; M1's parts from Seeed's 3D model; heights marked EST are the usual parts.
// ============================================================================

$fn = 64;

/* [1. Render Mode] */
// Print "test_fit" first: a ring of the shell at button height
part_to_render = "both"; // ["both", "top", "bottom", "test_fit"]
enable_camera_cutout = false; // open the port over M1's camera (the lens looks straight up)
recess_reset_btn     = true;  // true = slightly recessed against accidental resets

/* [2. Boards (mm, EB coordinates)] */
eb_l = 58.0;  eb_w = 42.5;  eb_t = 1.6;
xiao_c   = [10.54, 21.25];    // M1's centre; its USB-C faces the -X end
m1_l     = 21.0;              // XIAO length
m1_h     = 26.0;              // EB top to the top of M1 + Sense: Seeed's model + 11mm of sockets gives 24.7
mic_on_m1 = [12.8, 1.9];      // M1's mic, from M1's USB-C edge and front edge (CHECK)
mic_top  = 22.5;              // top of the mic above EB's top (CHECK)
cam_on_m1 = [3.53, 8.9];      // camera lens, from the same edges (Seeed's 3D model)
// From Seeed's 3D model of the Sense (the owner's side photo agrees within ~0.5mm); heights from M1's underside
m1_under = 11.0;              // EB top to M1's underside: the sockets (photo: ~10.7-11.6)
m1_under_tol = 0.5;           // how far that may be off, either way
cam_box  = [8.0, 7.95, 13.71]; // camera housing: across, from, to in height. It rests on the card
cam_over = 0.47;              // how far it overhangs M1's USB-C edge
card     = [3.11, 11.18, 6.6, 8.25];  // microSD card: past M1's USB-C edge, across, from, to in height
card_side = 1.3;              // it sits 0.8 off M1's centre line, to a side the model doesn't tell: room both ways
antenna  = [36, 18];          // the Wi-Fi antenna's foil (15 Oct); its U.FL cable is 55mm
usb      = [8.94, 1.53, 2.1]; // USB-C receptacle: width, past M1's edge, centre above M1's underside (Seeed)
usb_plug = [12.35, 6.5];      // the largest USB-C plug overmold the USB spec allows
usb_z    = m1_under + usb[2]; // USB-C centre above EB's top
oled_c   = [34.8, 21.25];
oled_aa  = [21.74, 10.86];    // active area
user_x   = 49.0;              // D1 (K1) on the front edge; measured 49-50
switch_x = 15.0;              // power switch (SW1) on the back edge; measured: lever at 13 on, 15.5 off
reset_x  = 9.0;               // reset (K2) on the front edge, in line with D1; measured 9-10
side_z   = 1.8;               // centre of the edge buttons and switch above EB's top
plunger  = 0.9;               // how far D1's and reset's plungers reach past EB's edge (Seeed's footprint; measured ~0.9)
lever    = 1.57;              // how far the switch lever reaches past EB's back edge (Seeed's footprint; measured ~1.5)
holes    = [[3.99, 3.75], [53.99, 3.75], [3.99, 38.75], [53.99, 38.75]];  // EB's 3mm corner holes
pin_holes   = [1, 2];         // the diagonal pair that gets locating pins
screw_holes = [0, 3];         // the other pair: screws up through EB into the shell
low_parts_h = 8.5;            // tallest part under the lower deck: the 2x4 pin header
under_eb_h  = 4.0;            // under EB: the coin cell holder (3.2) and pin tails (EST 2.5)
cell = [40, 30, 6];           // 603040 flat LiPo, JST-PH 2.0 plug (the bay takes up to 43 x 32 x 6.5)
bat_jack_y = 10.94;           // EB's battery socket (J2): its centre, on EB's far end, facing out

function eb(p) = [p[0] - eb_l/2, p[1] - eb_w/2];

/* [3. Enclosure] */
wall     = 2.4;
corner_r = 5.0;
gap      = 0.6;     // board edge to wall
ext      = 8.0;     // the far end's extra length: the battery plug and the turn of its wires (~7)
align    = 0.3;     // how far the shell may sit off EB: pin in EB's hole 0.15 + pin in the post's socket 0.15
clear    = 0.3;     // grooves: room beyond that
air      = 0.7;     // above the tallest parts

x_usb_in  = -eb_l/2 - gap;        x_usb_out = x_usb_in - wall;     // the USB-C end
x_far_in  =  eb_l/2 + gap + ext;  x_far_out = x_far_in + wall;     // the far end
inner_l = x_far_in - x_usb_in;    outer_l = x_far_out - x_usb_out;
inner_w = eb_w + 2*gap;           outer_w = inner_w + 2*wall;
cx = (x_usb_out + x_far_out) / 2;  // the box's centre in plan (EB's is 0)

z_eb  = wall + cell[2] + under_eb_h + 0.5;   // EB's underside: plate, cell, parts under EB, air
z_ebt = z_eb + eb_t;
low_roof    = z_ebt + low_parts_h + air;
turret_roof = z_ebt + m1_h + air;
lower_deck_h = low_roof + wall;
m1_turret_h  = turret_roof + wall;
step_in  = eb(xiao_c)[0] + m1_l/2 + 0.8;      // inside face of the step: clears M1
step_out = step_in + wall;
z_btn = z_ebt + side_z;

m1_corner = eb(xiao_c) - [m1_l/2, 17.8/2];
mic = m1_corner + mic_on_m1;
cam = m1_corner + cam_on_m1;
oled = eb(oled_c);
m1_edge = m1_corner[0];                      // M1's USB-C edge (0.04 in from EB's)

// The screen's open window: active area plus 1.2mm. Its -X side is the step's face,
// so no thin strip of roof is left between them.
win_x0 = (oled[0] - oled_aa[0]/2 - 1.2 - step_out < 2) ? step_out : oled[0] - oled_aa[0]/2 - 1.2;
win_x1 = oled[0] + oled_aa[0]/2 + 1.2;
win_hw = oled_aa[1]/2 + 1.2;

// Screws: M2 x 20 countersunk, from underneath
screw_len = 20;
pilot_d = 1.7;  pass_d = 2.4;  head_d = 4.2;
standoff_r = 2.25;   post_r = 2.2;   column_r = 2.4;
pin_d = 2.7;   pin_up = 1.0;     // locating pins: through EB and 1mm into the shell's posts
columns = [for (s = [-1, 1]) [x_far_in - (corner_r - wall), s * (inner_w/2 - (corner_r - wall))]];

// ============================================================================
// RENDER CONTROLLER
// ============================================================================
if (part_to_render == "both") {
    color("#1B3B5A") top_stepped_shell();
    color("#2C3E50") bottom_battery_tray();
} else if (part_to_render == "top") {
    rotate([180, 0, 0]) top_stepped_shell();
} else if (part_to_render == "bottom") {
    bottom_battery_tray();
} else if (part_to_render == "test_fit") {
    intersection() {
        top_stepped_shell();
        translate([cx, 0, z_btn]) cube([outer_l + 4, outer_w + 4, 12], center = true);
    }
}

// ============================================================================
// MODULE: TOP STEPPED SHELL
// ============================================================================
module top_stepped_shell() {
    union() {
        difference() {
            union() {
                translate([cx, 0, 0]) rounded_box(outer_l, outer_w, lower_deck_h, corner_r);
                translate([(x_usb_out + step_out)/2, 0, 0])
                    rounded_box(step_out - x_usb_out, outer_w, m1_turret_h, corner_r);
            }

            // The plate is the floor: the shell sits on it
            translate([-2*outer_l, -outer_w, -1]) cube([4*outer_l, 2*outer_w, wall + 1]);

            // Cavities: open at the bottom for the plate
            translate([cx, 0, -0.1])
                rounded_box(inner_l, inner_w, low_roof + 0.1, corner_r - wall);
            translate([(step_in + x_usb_in)/2, 0, -0.1])
                rounded_box(step_in - x_usb_in, inner_w, turret_roof + 0.1, corner_r - wall);

            // Dark glass recess + OLED window
            translate([win_x0, oled[1] - win_hw - 2, lower_deck_h - 1.0])
                cube([win_x1 + 2 - win_x0, 2*win_hw + 4, 1.1]);
            translate([win_x0, oled[1] - win_hw, low_roof - 1])
                cube([win_x1 - win_x0, 2*win_hw, wall + 2]);

            // Mic ports, "Three Goals" 3-dot array: the middle one goes through over the mic
            // (sealed by the duct below), the outer two are 1mm blind dots
            translate([mic[0], mic[1], turret_roof - 1]) cylinder(d = 1.6, h = wall + 2);
            for (dy = [-3.5, 3.5])
                translate([mic[0], mic[1] + dy, m1_turret_h - 1]) cylinder(d = 1.6, h = 2);

            // Camera: an eyelid ring over the lens on top, a port when enabled
            translate([cam[0], cam[1], m1_turret_h - 0.6]) difference() {
                cylinder(d = 11.0, h = 1);
                translate([0, 0, -0.1]) cylinder(d = 9.0, h = 1.2);
            }
            if (enable_camera_cutout)
                translate([cam[0], cam[1], turret_roof - 1]) cylinder(d = 7.5, h = wall + 2);

            // USB-C: room for any plug the USB spec allows (overmold up to 12.35 x 6.5), with M1's
            // height off by up to m1_under_tol, the shell off by align, and 0.4 round that; the plug
            // reaches 1.5mm in to meet the socket
            translate([x_usb_out - 1, eb(xiao_c)[1], z_ebt + usb_z])
                rotate([0, 90, 0]) linear_extrude(wall + 2)
                    offset(r = 0.8) square([usb_plug[1] + 2*(m1_under_tol + 0.4) - 1.6,
                                            usb_plug[0] + 2*(align + 0.4) - 1.6], center = true);

            // Power slide switch: slot + finger bevel, back wall
            translate([eb([switch_x, 0])[0], outer_w/2 - wall/2, z_btn]) {
                cube([9.0, wall + 2, 4.2], center = true);
                translate([0, 1.0, 0]) cube([12.0, 2.0, 6.5], center = true);
            }

            // D1 and reset, both on the front wall
            translate([eb([user_x, 0])[0], -outer_w/2, z_btn]) tongue_cut(12, 7, 3, dish = true);
            translate([eb([reset_x, 0])[0], -outer_w/2, z_btn])
                tongue_cut(7, 6, 1.5, recess = recess_reset_btn ? 0.4 : 0);

            // Grooves up the inside of the walls, from the bottom edge, for the parts that stick out
            // past EB's edges into the walls: the shell slides down past them. Behind the tongues they
            // are the pockets the plungers sit in, 0.6mm short of the tongue at rest (0.3-0.9 with align).
            for (x = [user_x, reset_x])                                     // D1's and reset's plungers
                groove([eb([x, 0])[0], -inner_w/2], [0, -1], 4.0, plunger - gap + align + clear, z_ebt + 3.0);
            groove([eb([switch_x, 0])[0], inner_w/2], [0, 1], 9.0, lever - gap + align + clear, z_btn);   // the lever
            groove([x_usb_in, eb(xiao_c)[1]], [-1, 0], usb[0] + 2*(align + clear),                  // the USB-C socket
                   usb[1] - (m1_edge - x_usb_in - gap) - gap + align + clear, z_ebt + usb_z);

            // M1's USB-C edge is flush with EB's, so the camera's overhang comes within 0.17mm of the end
            // wall: a pocket in its inside face, 0.6mm round the housing wherever M1's height puts it
            let(d = cam_over - (xiao_c[0] - m1_l/2) + align)
                if (d > 0) translate([x_usb_in - d, cam[1] - cam_box[0]/2 - gap - 0.7,      // 0.7: it sits 0.64 off M1's
                                      z_ebt + m1_under + cam_box[1] - gap - m1_under_tol])  // centre line, side unknown
                    cube([d + 0.1, cam_box[0] + 2*(gap + 0.7), cam_box[2] - cam_box[1] + gap + air + m1_under_tol]);

            // The microSD card runs out past the USB-C to about the wall's outside face: it goes in last,
            // through a slot in the end wall just above the USB-C opening and joined to it (15 Oct)
            translate([x_usb_out - 1, eb(xiao_c)[1] - card[1]/2 - card_side,
                       z_ebt + m1_under + card[2] - gap - m1_under_tol])
                cube([wall + 1.1, card[1] + 2*card_side, card[3] - card[2] + 2*(gap + m1_under_tol)]);

            // The antenna's foil is longer than any one wall of the tall end, so it goes round the inside
            // corner by the USB-C: along the front wall from the step, onto the end wall up to the card's
            // slot, high up and away from EB, in a 0.3mm recess that holds it in place (~0.6mm to spare)
            intersection() {
                translate([cx, 0, turret_roof - 0.5 - antenna[1] - 0.6]) difference() {
                    rounded_box(inner_l + 0.6, inner_w + 0.6, antenna[1] + 0.6, corner_r - wall + 0.3);
                    translate([0, 0, -1]) rounded_box(inner_l - 0.2, inner_w - 0.2, antenna[1] + 2.6, corner_r - wall - 0.1);  // into the cavity: no shared faces
                }
                translate([x_usb_out - 1, -outer_w/2 - 1, 0])
                    cube([step_in - x_usb_out + 0.99, eb(xiao_c)[1] - card[1]/2 - card_side + outer_w/2 + 0.99, 2*turret_roof]);  // 0.01 short: no shared faces
            }
        }

        // Mic duct down to 1mm above the mic; a foam ring seals it to the mic
        translate([mic[0], mic[1], z_ebt + mic_top + 1]) difference() {
            cylinder(r = 2.0, h = turret_roof - (z_ebt + mic_top + 1) + 0.01);
            translate([0, 0, -0.1]) cylinder(d = 1.6, h = 10);
        }

        // Posts down from the roof onto EB's corners, clamping it against the plate's standoffs. Over
        // the pins: a socket that takes the pin's tip and lines the shell up with EB. Over the screw
        // holes: a pilot for the screw that comes up through the plate and EB.
        for (i = [0 : 3]) let(p = eb(holes[i]), roof = p[0] < step_in ? turret_roof : low_roof)
            translate([p[0], p[1], z_ebt]) difference() {
                cylinder(r = post_r, h = roof - z_ebt + 0.01);
                if (len(search(i, pin_holes)) > 0) {
                    translate([0, 0, -0.1]) cylinder(d = pin_d + 2*0.15, h = pin_up + 0.4);
                    translate([0, 0, -0.1]) cylinder(d1 = pin_d + 0.9, d2 = pin_d + 2*0.15, h = 0.4);
                } else
                    translate([0, 0, -0.1]) cylinder(d = pilot_d, h = screw_len - (z_ebt - 0.5) + 1.5);
            }

        // Columns in the far end's corners for the other two screws: clearance, then 7mm of pilot
        for (c = columns) translate([c[0], c[1], wall]) difference() {
            cylinder(r = column_r, h = low_roof - wall + 0.01);
            translate([0, 0, -0.1]) cylinder(d = pass_d - 0.2, h = screw_len - 0.5 - wall - 7 + 0.1);
            translate([0, 0, -0.1]) cylinder(d = pilot_d, h = screw_len - 0.5 - wall + 1.5);
        }
    }
}

// A groove in the inside face of a wall, from the shell's bottom edge up to z1: centred at c on the
// wall's inside face, n pointing out through the wall, w wide, d deep
module groove(c, n, w, d, z1) {
    translate([c[0], c[1], wall - 0.1])
        rotate([0, 0, atan2(n[1], n[0]) - 90])
            translate([-w/2, -0.1, 0]) cube([w, d + 0.1, z1 - wall + 0.1]);
}

// ============================================================================
// SIDE-BUTTON FLEX TONGUE (case.py's start/stop): local x along the wall, y into the
// case from the outer face, z up, origin on the button. Hinged at its -x end, so it
// bends along the print layers; the hinge is thinned to 0.7mm.
// ============================================================================
module tongue_cut(len, h, tip, dish = false, recess = 0) {
    x0 = -(len - tip);
    difference() {
        translate([x0, -1, -h/2 - 0.6]) cube([len + 0.6, wall + 2, h + 1.2]);
        translate([x0 - 0.1, -2, -h/2]) cube([len + 0.1, wall + 4, h]);
    }
    translate([x0, 0.7, -h/2]) cube([1.5, wall, h]);
    if (dish) translate([0, -3.0, 0]) sphere(d = 8.0);             // 1mm deep, 5.3mm across
    if (recess > 0) translate([x0 + 1.5, -1, -h/2]) cube([tip - x0 - 1.5, 1 + recess, h]);
}

// ============================================================================
// MODULE: BOTTOM PLATE + BATTERY BAY: the case's floor, with the standoffs EB sits on
// ============================================================================
module bottom_battery_tray() {
    bay = [cell[0] + 3, cell[1] + 2];          // the cell's bay, inside its ribs
    difference() {
        union() {
            translate([cx, 0, 0]) rounded_box(outer_l, outer_w, wall, corner_r);
            // Standoffs under EB's corner holes, up to EB's underside; pins on the diagonal pair
            for (i = [0 : 3]) let(p = eb(holes[i])) translate([p[0], p[1], wall - 0.01]) {
                cylinder(r = standoff_r, h = z_eb - wall + 0.01);
                if (len(search(i, pin_holes)) > 0) {
                    cylinder(d = pin_d, h = z_eb - wall + eb_t + pin_up - 0.4);
                    translate([0, 0, z_eb - wall + eb_t + pin_up - 0.41])
                        cylinder(d1 = pin_d, d2 = pin_d - 0.8, h = 0.4);
                }
            }
            // Low ribs round the cell, open at the far end for its wires
            translate([0, 0, wall - 0.01]) difference() {
                translate([-bay[0]/2 - 1, -bay[1]/2 - 1, 0]) cube([bay[0] + 2, bay[1] + 2, 2.0]);
                translate([-bay[0]/2, -bay[1]/2, -1]) cube([bay[0], bay[1], 4]);
                translate([bay[0]/2 - 1, -bay[1]/2 + 4, -1]) cube([3, bay[1] - 8, 4]);
            }
        }
        // Screw holes, countersunk from below: two up through the standoffs and EB, two into the columns
        for (p = concat([for (i = screw_holes) eb(holes[i])], columns)) translate([p[0], p[1], -0.1]) {
            cylinder(d = pass_d, h = z_eb + 1);
            cylinder(d1 = head_d, d2 = pass_d, h = 1.0);
        }

        // 8 Octo-Suction Foot Dimples
        for (i = [0 : 7]) {
            angle = i * 45;
            translate([cx + cos(angle)*18, sin(angle)*13, -0.1])
                cylinder(d = 6.2, h = 1.0);
        }
    }
}

module rounded_box(l, w, h, r) {
    hull() {
        for (x = [-(l/2 - r), (l/2 - r)])
            for (y = [-(w/2 - r), (w/2 - r)])
                translate([x, y, 0])
                    cylinder(r = r, h = h);
    }
}
