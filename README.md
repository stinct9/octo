# Octo

A recorder for your conversations, and a lab on your own Mac that reviews them against principles you
write yourself.

- **The recorder** sits on the table in plain view, records to its SD card, and sends what it recorded to your
  Mac over your Wi-Fi. A screen and a light show when it's recording. A phone app sets its Wi-Fi networks and
  starts or stops it over Bluetooth.
- **The lab** runs on your Mac (Apple Silicon): it finds the conversations, writes down who said what, and
  reviews each one: how the others acted, what went unsaid, your conduct against your principles, and small
  next steps. A coach reads across your reviews; a page per person helps before you talk to them.
- **Your principles are your own.** Octo never writes them: setup shows two examples you can start from if you tap
  them, and the reviews hold you to your words.

## Privacy

Audio, voiceprints and transcripts stay on your Mac. When a conversation is reviewed, its text (the transcript,
names and numbers) goes to the model you chose: Claude Code on your own Claude plan, a service on your own API key,
or a model running on your Mac. There are no accounts and no servers of ours. In a new lab, someone else's voice is recognised only once you've
recorded that they agreed; the recorder never records before it's paired with your Mac, and its chunks are
encrypted so only your Mac can read them.

Recording people may need their consent where you live. That's yours to know and follow. Everything that can leave
your Mac, and when: [PRIVACY.md](PRIVACY.md).

## Install

**What you need:** a Mac with Apple's M chip (M1 or later, 2020 on), 16 GB of memory, macOS 14 or later and about
20 GB free (35 GB with the iPhone app). Set aside an hour; most of it is downloads you can leave running.

1. Open **Terminal**: press ⌘ Space, type `Terminal`, press Return.
2. Paste this line and press Return:

   ```bash
   curl -fsSL https://github.com/stinct9/octo/releases/latest/download/install.sh | bash
   ```

   It downloads the latest ready-built release (nothing to build: no git, no Node), checks it against the SHA-256 in
   the release's version.txt, and installs it. If it stops part
   way (the Mac went offline, say), it tells you where: paste the same line again, and it carries on.
3. If a window from Apple asks to install "command line developer tools", click **Install**, then **Agree**. Terminal
   waits for it.
4. When Terminal says it's done, the **Octo** app opens on **Set up Octo**. You can close Terminal. Follow the page
   from top to bottom; it tells you what to do at each step and checks it worked:
   1. **This Mac**: checked for you.
   2. **Speech models** (about 10 GB, downloading while you carry on): a free [Hugging Face](https://huggingface.co)
      account, a token you paste, and two models' conditions to accept. The page shows where to click.
   3. **You**: your name.
   4. **Your principles**, in your own words. Octo never writes them; two examples are there if you want to start
      from one.
   5. **A model for reviews**: Claude Code on your own Claude Pro or Max plan (the page gives the two lines to paste),
      or an API key (Anthropic's, billed per use, or any OpenAI-compatible service), or a model on your Mac.
   6. **The iPhone app** (optional): Xcode from the App Store and your Apple ID (a free one works).
   7. **Connect the app to this Mac** and **your lab away from home** (both optional).
   8. **Your voice**: confirm yourself in your first conversation.

**Updates:** when there's a new release, the octopus in the menu bar says **Octo update ready**. Click it; your
conversations and settings are kept. A release goes out when the maintainer cuts one, after the tests pass on it, not
on every change; each is dated (`2026.11.05-2+…`), and a lab offers only a newer one. If an update misbehaves,
`~/Library/Application\ Support/octo/app/.venv/bin/octo update --back` returns to the version before.

More detail (what each step needs, updating, uninstalling, installing from a git clone to change the code):
[docs/install.md](docs/install.md). The iPhone app by hand: [mobile/README.md](mobile/README.md). The recorder's
Bluetooth: [docs/recorder-ble.md](docs/recorder-ble.md).

## In this repository

| | |
|---|---|
| `octo/` | the lab: the pipeline, the review, the coach, the web API |
| `ui/` | the lab's web pages (Vue) |
| `mobile/` | the phone app (Vue + Capacitor) |
| `firmware/recorder/` | the recorder's firmware (Seeed XIAO ESP32S3 Sense on the XIAO expansion board) |
| `hardware/case/` | the table case, as a parametric model |
| `bench/` | bench tests for the boards |

## Contributing

Issues and pull requests are welcome: [CONTRIBUTING.md](CONTRIBUTING.md) and the
[code of conduct](CODE_OF_CONDUCT.md). Security problems privately, please: [SECURITY.md](SECURITY.md).

## Credits

Octo runs these models on your Mac; thank you to their authors.

- [Qwen3-ASR-1.7B](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) and its forced aligner, Qwen team (Apache-2.0)
- [Whisper large-v3](https://huggingface.co/mlx-community/whisper-large-v3-mlx), OpenAI (MIT)
- [pyannote speaker-diarization-3.1 and segmentation-3.0](https://huggingface.co/pyannote/speaker-diarization-3.1),
  Hervé Bredin and contributors (MIT)
- [WeSpeaker ResNet34 and ResNet293](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet293-LM),
  the WeSpeaker team, trained on VoxCeleb (CC-BY-4.0)
- [Silero VAD](https://github.com/snakers4/silero-vad) (MIT)

## Licence

- The software (the lab, the pages, the phone app and the recorder's firmware): [AGPL-3.0](LICENSE).
- The hardware (the case and its drawings, `hardware/`): [CERN-OHL-S-2.0](hardware/LICENSE).

Both are copyleft. A changed copy has to stay open, including one run as a service, so nobody can sell a closed
version that drops "it stays on your Mac".

## Support

Octo is free. Ways to support it are coming once the code is public. To back it, or to talk about investing,
write to support@octolab.online.
