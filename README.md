# Octo

A recorder for your conversations, and a lab on your own Mac that reviews them against principles you
write yourself.

- **The recorder** sits on the table in plain view, records to its SD card, and sends what it recorded to your
  Mac over your Wi-Fi. A screen and a light show when it's recording. A phone app sets its Wi-Fi networks and
  starts or stops it over Bluetooth.
- **The lab** runs on your Mac (Apple Silicon): it finds the conversations, writes down who said what, and
  reviews each one: how the others acted, what went unsaid, your conduct against your principles, and small
  next steps. A coach reads across your reviews; a page per person helps before you talk to them.
- **Your principles are your own.** Octo never writes or suggests them: the reviews hold you to your words.

## Privacy

Audio, voiceprints and transcripts stay on your Mac. When a conversation is reviewed, its text (the transcript,
names and numbers) goes to the model you chose: Claude Code on your own Claude plan, a service on your own API key,
or a model running on your Mac. There are no accounts and no servers of ours. In a new lab, someone else's voice is recognised only once you've
recorded that they agreed; the recorder never records before it's paired with your Mac, and its chunks are
encrypted so only your Mac can read them.

Recording people may need their consent where you live. That's yours to know and follow. Everything that can leave
your Mac, and when: [PRIVACY.md](PRIVACY.md).

## Install

You need an Apple Silicon Mac (M1 or later) with 16 GB of memory, macOS 14 or later and about 20 GB free, a free
Hugging Face account, and a model for the reviews (Claude Code with your Claude plan, your own API key, or a model on
your Mac). Nothing else first:
the installer gets Python, and Node if the Mac has none. In Terminal:

```bash
git clone https://github.com/stinct9/octo.git ~/Octo
~/Octo/install.sh
```

It starts the lab when you log in, builds the **Octo** app and opens **Set up Octo**, which walks you through the
rest while the speech models (about 10 GB) download. Details, updating and uninstalling:
[docs/install.md](docs/install.md).

**The iPhone app**, built with your own Xcode and Apple ID (a free one works; the install lasts 7 days): plug the
phone in, then `~/Octo/.venv/bin/octo phone install`. Steps: [mobile/README.md](mobile/README.md). The recorder's
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
