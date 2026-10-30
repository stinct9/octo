# The recorder over Bluetooth (rec-12; rec-15)

What the phone app (`mobile/`, product plan 4.5, `docs/phone-plan.md`) and the recorder's firmware
(`firmware/recorder/recorder.ino`, "Bluetooth for the phone app") agree on. Change both together.

## Finding it

- Name: `Octo XXXX` (the last two bytes of its Wi-Fi MAC address, in hex, from the chip's fuses). rec-12 and
  rec-13 read it from the Wi-Fi driver before Wi-Fi had started, so a recorder paired at boot was `Octo 0000`.
- Only once paired with a Mac (`octo device pair`): before that it has no Bluetooth and shows "Set me up".
- **Advertising, rec-15** (Jay, 8 Oct): every 152.5 ms for a minute after it starts, after a tap of its button
  and after a phone disconnects; then every 1022.5 ms for as long as a phone is paired, so the app reconnects
  whenever it's opened; not at all with no phone paired once the window has closed. Never while a phone is
  connected. rec-12 to rec-14: for 10 minutes after starting and after a tap, then not at all.
- Before a restart it chose (after an update, or going back), rec-15 sends one last status with `restart`
  true, stops advertising and ends the link, so the app knows at once why it went.

## Pairing

- LE Secure Connections with passkey entry. The recorder has a screen and no keyboard (DisplayOnly): when a
  phone first reads or writes a value, the recorder shows **six digits** ("Phone code") and the phone's
  system asks for them. A new code each time.
- **New phones only within 10 minutes of a tap** (rec-15), or of starting while no phone is paired yet (the
  first phone needs no tap). Outside that the recorder shows no code, so it can't be paired by someone who
  can only see it.
- Bonded: the phone stays paired. To forget it, forget "Octo XXXX" in the phone's Bluetooth settings.
- Every value below needs an encrypted, authenticated (MITM) link: nothing is readable or writable before
  pairing. rec-15 also ends any link that encrypts without the code ("Just Works") and deletes its bond, and
  notifies only links paired with the code.
- The recorder keeps up to 3 paired phones.

## Service `6f63746f-0001-4c61-8000-6f63746f7265`

| Characteristic | UUID | Properties | Value |
|---|---|---|---|
| Status | `6f63746f-0002-4c61-8000-6f63746f7265` | read, notify (every 2 s while connected; rec-15: at once when it changes) | UTF-8 JSON, below |
| Control | `6f63746f-0003-4c61-8000-6f63746f7265` | write | `on` or `off`: recording on or off (kept across restarts; rec-15 refuses `on` during an update); rec-24: `filler on` or `filler off`, the filler cue (kept; ignored by a build without the owner's model) |
| Networks | `6f63746f-0004-4c61-8000-6f63746f7265` | read, write | read: JSON array of names, newest (tried first) first; write: below |

Status (rec-24, a build with the owner's filler model) adds `"filler_model":true,"filler_on":false,"fillers":0` (this
recording's "you know"s). The table hasn't changed since rec-12. When it does, bump `BLE_GATT_REV` in the firmware: the recorder then
sends Service Changed once, so paired phones drop their cached copy.

**Status, a read** (rec-15; every field rec-12 had is still there, so an older app reads it):

```json
{"v":2,"fw":"rec-15","boot":46,"state":"recording","want_on":true,"recording":true,"rec_s":754,"upd":-1,
 "restart":false,"pending":false,"wifi":true,"rssi":-61,"name":"Octo F73C","card_free_mb":28120,
 "card_total_mb":29600,"ssid":"Home","networks":2,"clock_set":true,"battery_mv":-1,"rolled_back":false,
 "up_s":3600,"slot":"app1","i2s_lost":0,"heap_min":180000}
```

**Status, a notification** (rec-15): the live part only, under the 253 bytes an iPhone's link carries at
once: `v`, `fw`, `boot`, `state`, `want_on`, `recording`, `rec_s`, `upd`, `restart`, `pending`, `wifi`,
`rssi`. The app folds it into the last read. Before rec-15 a notification was the whole status (no `v`).

- `state` is one of `not_paired`, `mic_error`, `card_error`, `no_card`, `card_full`, `off`, `starting`,
  `recording`.
- `boot` counts every start: a new number means it restarted. `upd` is the percent of an update received, or
  -1. `restart` is true just before a restart it chose. `pending` is true while a new firmware waits for the
  Mac to confirm it ("Checking" on its screen). `rolled_back` is true for the whole of a boot that went back
  to the firmware before.
- `ssid` is escaped (rec-15: a network name with a quote broke every read before). `battery_mv` is -1 until
  the hardware can measure it. `i2s_lost` counts audio samples the microphone's driver dropped (rec-15).

**Networks**, written as UTF-8 lines separated by `\n`:

- `add\n<name>\n<password>`: save a network (2.4 GHz; a password of 8 to 63 characters, or empty for an
  open network). The newest is tried first; with five saved, the oldest goes.
- `del\n<name>`: forget one.

After a write, read Networks again for the list as it now is (rec-15 answers the write once the change is
made). A bad write changes nothing.

The same networks can be changed from the Mac (Settings → Recorder, or `octo device networks`).
