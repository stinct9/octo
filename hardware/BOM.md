# Build the recorder

A small recorder that sits on the table in plain view, records to its memory card, and sends what it recorded to
your Mac over your home Wi-Fi. Two boards that plug together (no soldering), a card, a battery and a printed case.
You don't need it: voice memos from your phone work without it.

## Parts

| Part | Notes |
|---|---|
| **Seeed Studio XIAO ESP32S3 Sense**, pre-soldered | The board with the microphone and the card slot. Comes with a camera module (unused, keep it on) and a U.FL Wi-Fi antenna |
| **Seeed Studio XIAO Expansion Board Base** | The screen (128 × 64 OLED), a button, the battery socket and an on/off switch |
| **microSD card, 32 GB** | FAT32. Larger cards work, but only 32 GB is used. Check it before trusting it (below) |
| **Flat LiPo battery, 603040** (6 × 30 × 40 mm), with a JST-PH 2.0 plug | Optional: on USB power it records as long as it's plugged in. Check its polarity (below) |
| **USB-C data cable** | A charge-only cable is the most common reason a board never shows up on the Mac |
| **The case** (optional) | `hardware/case/beta.scad`, printed in PETG; a 2 mm grey-smoke acrylic pane over the screen; 4 × M2 × 20 countersunk screws; a small foam ring for the microphone port. **Not printed and fitted yet**: the bare boards work without it |

Buy the boards from Seeed Studio or one of its distributors. Check the listing says *Sense* and *pre-soldered*.

## Check before you build

- **The card:** fake-capacity cards silently overwrite old recordings. Test it with `f3write` and `f3read`
  (`brew install f3`, or the F3 app) before you put it in.
- **The battery:** check its polarity with a multimeter against the socket's + and − marks. JST plugs are often
  wired the other way round from the socket.
- **The cable:** a USB-C cable that carries data.

## Put it together

1. **The antenna.** Hook one side of the U.FL connector onto the socket on the Sense board, then press the other side
   down until it clicks. It's rated for few fittings: fit it once and leave it. To remove it, lift from one side;
   never pull the cable. Without it Wi-Fi is weak to unusable: there's no antenna on the board itself.
2. **The card** goes in the Sense board's own slot (not the expansion board's). Push it fully in: a half-seated
   card looks inserted and fails.
3. **The boards.** Seat the XIAO in the middle of the expansion board's two female headers,
   *before* any power. A misaligned board can damage both.
4. **The battery** plugs into the expansion board's JST socket; the slide switch turns it off for real.
5. Never power the board while its pins are still in the black shipping foam: it's conductive and bridges them.

## Give it Octo's firmware and pair it

With the lab installed on your Mac (`docs/install.md`) and the recorder plugged in over USB:

```bash
.venv/bin/octo device flash
```

On a new board it installs Octo's firmware over USB: a release's prebuilt firmware, checked against its
`SHA256SUMS`, or built on your Mac if you have `arduino-cli`. Then pair it with this Mac and give it your Wi-Fi:

```bash
.venv/bin/octo device pair --ssid "Your Wi-Fi"
```

Pairing gives it its keys over USB: from then on its recordings are encrypted so only your Mac can read them, and
every request between them is signed. It won't record until it's paired. Later updates go over Wi-Fi or USB, show
their progress on its screen, and go back to the firmware before if the new one doesn't start.

If the board won't take the firmware: hold **B** (beside the USB-C), tap **R**, let go of **B**, and run
`octo device flash` again.

## Using it

- **Record:** hold the expansion board's button for a second to start or stop; a tap shows what it's doing.
- **The screen** is the recording light: always on, dim, with REC and the time while it records. There's no hidden mode.
- **At home** your Mac collects what it recorded every 5 minutes. Away, it keeps recording to the card.
- **The phone app** (optional) finds it over Bluetooth, pairs with a six-digit code from its screen, adds Wi-Fi
  networks and starts or stops it: `mobile/README.md`.

## Licence

The case and its drawings are under the CERN-OHL-S-2.0 (`hardware/LICENSE`); the firmware, like the rest of
the software, under the AGPL-3.0.
