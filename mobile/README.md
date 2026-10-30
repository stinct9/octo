# Octo's phone app

Find the recorder over Bluetooth and see what it's doing (recording, card, Wi-Fi), start or stop recording,
and keep its Wi-Fi networks (up to five). It talks only to the recorder: nothing leaves the phone.

Vue 3 + Capacitor 8: one codebase for iPhone and, later, Android. The recorder's side is
`firmware/recorder/recorder.ino` (rec-12); what the two agree on is `docs/recorder-ble.md`.

## Try it without a recorder

```bash
npm install
npm run dev
```

Then open http://127.0.0.1:5181/?demo for a simulated recorder (`src/fake.ts`, same rules as the firmware).
`npm test` runs the protocol tests.

## On an iPhone

Bluetooth doesn't work in the iOS Simulator: it needs a real iPhone.

**One command** once Xcode is signed in to your Apple ID and the phone is plugged in with Developer Mode on:
`.venv/bin/octo phone install` from the lab's folder (octo/iphone.py; docs/install.md, *The iPhone app*). It does
steps 2 and 3 below without opening Xcode, with an app ID of your own (`app.octo.recorder.<team>`). By hand:

1. Install **Xcode** from the Mac App Store, open it once, and sign in with your Apple ID
   (Xcode → Settings → Accounts).
2. `npm run ios`: builds the pages, copies them into `ios/`, and opens the project in Xcode.
3. In Xcode, pick your iPhone (plugged in, or on the same Wi-Fi), set **Signing → Team** to your Apple ID,
   and press Run. A free Apple ID installs it on your own phone for a week at a time. TestFlight (to give it to
   beta testers) needs the paid Apple Developer Program.
4. The first time only, on the iPhone: turn on **Developer Mode** (Settings → Privacy & Security), and after the
   install trust yourself as its developer (Settings → General → VPN & Device Management → your Apple ID →
   Trust). Until then the phone installs the app but won't open it, and Xcode doesn't say why.
5. If Xcode offers to "update to recommended settings", decline it: it raises the minimum iOS version.

Change `appId` in `capacitor.config.ts` (and the bundle ID in Xcode) to your own reverse-DNS name before
registering the app with Apple.

## Pairing

The recorder must be paired with its Mac first (Settings → Recorder in the lab, or `octo device pair`). Then:
tap the recorder's button, press **Find the recorder**, choose it, and type the six digits it shows when
the phone asks. The phone stays paired after that.
