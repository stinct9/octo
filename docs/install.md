# Installing Octo's conversation lab

The lab runs on your own Mac. Your recordings, voiceprints and transcripts stay on it; the only thing
that leaves is a conversation's text when it's reviewed, sent to the model you choose (Claude Code on your own Claude plan, or a service on your own key).

## What you need

- **An Apple Silicon Mac** (M1 or later) with **16 GB** of memory and **macOS 14** or later. The speech
  models run on the Mac's own GPU.
- **About 20 GB free**: 10 GB of speech models, downloaded once, and room for audio. The installer stops with
  under 15 GB, and `octo doctor` warns under 20.
- Nothing else to install first: no Homebrew, git or Node. The installer gets **uv** (Astral's Python
  installer, one file in `~/.local/bin`) unless a recent one is already there, and uv brings its own
  Python 3.12, whatever Python the Mac already has. It also asks for Apple's **command line
  tools** if they're missing (Apple's own installer opens; only the recorder needs them).
- **A free Hugging Face account** (https://huggingface.co), for two of the speech models: their authors ask
  you to accept their conditions before downloading.
- **A model for the reviews**, one of:
  - **Claude Code on this Mac, on your Claude Pro or Max plan**: install Claude Code
    (`curl -fsSL https://claude.ai/install.sh | bash`, version 2.1.280 or later) and log in with
    `claude auth login`. No bill per use: reviews count against your plan's limits, shared with your own Claude
    use (at most 60 calls a day). Your plan's terms with Anthropic apply.
  - **An Anthropic API key** (console.anthropic.com), **billed per use**: with Claude Opus 5.5 a review has cost
    about $0.10 plus $0.006 a minute of conversation, paid to Anthropic.
  - Any OpenAI-compatible service (OpenAI, OpenRouter, Google, a model server on your Mac), or a local model
    through Ollama.

## Install

1. Run the one-line installer in Terminal (⌘ Space, `Terminal`, Return):

   ```bash
   curl -fsSL https://github.com/stinct9/octo/releases/latest/download/install.sh | bash
   ```

   It downloads the latest release from GitHub (the code with its pages built), checks it against the SHA-256 in
   the release's version.txt, and puts it in its own folder, `~/Library/Application Support/octo/versions/<version>`,
   keeps what the lab records beside it in `lab.noindex` (only you can open it), and runs `install.sh` there. Once
   that has built it, `octo/current` points at it (and `octo/app`, where it lived before 5 Nov, at `current`). The script is readable at that address first, if you'd rather look before you run
   it. Updates then come from GitHub's latest release: **Octo update ready** in the octopus menu.

   **Beta testers** paste the line they were sent instead: the same installer pointing at the beta's release, and
   updates come from there.

   **From a git clone instead** (to change the code): `git clone <the repository's URL> ~/Octo && ~/Octo/install.sh`.
   A clone has no built pages: the installer builds them with the Mac's Node, or fetches a pinned Node (27 MB, checked
   against its checksum) into `.node` if there's none. Update with `~/Octo/.venv/bin/octo update`. Git comes with
   Apple's command line tools (`xcode-select --install`).

   Not in `~/Downloads`, `~/Documents`, `~/Desktop` or iCloud Drive: macOS asks before a program reads those,
   and the lab, started at login, has no window to answer in, so it would wait with no sign. The installer
   refuses them and says so.

   **If it stops** (the Mac went offline, a download was cut short), it says at which step: run the same line again.
   What's done is kept, and the code is replaced cleanly, so a second run also repairs a broken install. Before it
   touches anything it checks the Mac (Apple Silicon, macOS 14, free disk space), the folder, and port 8765; in a clone,
   `./install.sh --check` runs only those checks.

   **The `octo` command** this page names lives inside the install, not on your PATH. From the one-line install,
   type it in full, for example `~/Library/Application\ Support/octo/app/.venv/bin/octo doctor`; in a clone, it's
   `.venv/bin/octo` from inside the clone's folder.

   **One lab per Mac.** The lab answers on 127.0.0.1:8765 and its helpers on 8767 (and Ollama's 11434), the same for
   every user, so a second macOS user on the same Mac can't run a lab while the first one's is running: the installer
   says so and stops. The lab is per user otherwise (its login items, folder and Octo app are that user's own).

2. It finishes by starting the lab when you log in, building the **Octo** app (below) and opening **Set up Octo** in
   it. The speech models (about 10 GB) download in the background meanwhile. Two of them
   (`pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`) need a free Hugging Face account: the
   page's *Speech models* section walks you through it and shows which is accepted:
   1. Make an account at https://huggingface.co/join (confirm the email they send).
   2. Make a **Read** token at https://huggingface.co/settings/tokens/new?tokenType=read and paste it on the page.
   3. Signed in to Hugging Face in your browser, open https://huggingface.co/pyannote/speaker-diarization-3.1 and
      https://huggingface.co/pyannote/segmentation-3.0. Near the top of each is a box saying you need to agree to share
      your contact information. Fill in **Company/university** (where you work or study, or "Personal") and **Website**
      (your company's site, or any page about you), then click **Agree and access repository**. The box then says you
      were granted access. pyannote's form says they may email you now and then about their paid services.

   The page checks every few seconds and starts the download once both are accepted. Then: who you are, your
   principles in your own words, the model for reviews (Claude Code on your plan, or a key), and your voice (confirm yourself in your first
   conversation).

## The Octo app

`install.sh` builds a small Mac app on your Mac, `Octo` in /Applications (in ~/Applications if you can't write to
/Applications without a password). It's built from `macapp/Octo.swift` with Apple's own Swift compiler from the
command line tools, so there's no download to trust and nothing for Gatekeeper to check. It's only a window: the lab
itself is the program the login item starts, and quitting the app doesn't stop it.

- **The window** shows the lab's pages. It lets itself in with your Mac's key (no "Let this browser in"), after the
  lab has proved it holds that key, so the key is never sent to another program on port 8765.
- **The octopus in the menu bar** says what needs you (conversations to review, speakers to confirm, a failed step,
  setup steps left), whether the recorder is recording, and when the work in hand will be ready. A dot means
  something needs you; dimmed means the lab isn't answering (Restart the Lab is there). **Open in Browser** lets
  your default browser in instead. **Open at Login** (off until you turn it on) keeps the octopus there after a
  restart.
- **Notifications** come from Octo while the app is open (macOS asks once, the first time there's something to
  say), and from Script Editor while it isn't. Counts only, never what was said.
- Without the command line tools, `install.sh` makes a one-line launcher instead that opens the lab in your browser;
  install the tools (`xcode-select --install`) and run `octo launcher install` to get the app.
  `octo doctor` says which you have and whether it's up to date.
- On macOS 14 or 15 the command line tools are older, and the app is built without the live filler cue on calls,
  which needs macOS 26 (its speech recognition is new there); everything else is the same.

## The iPhone app

**Set up Octo** walks through it, one part at a time, each checked on this Mac before the next: get **Xcode** from the
App Store (Apple's free app for making iPhone apps; Octo uses it to sign its app with your Apple ID), open it once (Agree,
and download its iOS part), sign in to it with your Apple ID (a free one works), plug the iPhone in and tap **Trust**,
turn on **Developer Mode** on the phone (it restarts), press **Put Octo on my iPhone**, then on the phone Settings →
General → VPN & Device Management → Developer App → your Apple ID → **Trust**.

**With a free Apple ID an install opens for 7 days.** The lab renews it by itself 2 days before, while the iPhone is
plugged in or on the same Wi-Fi, and sends a notification if a day is left and it couldn't. Each Apple ID gets its own
app ID (`app.octo.recorder.<team>`), since Apple keeps one per team. In Terminal, `octo phone install` does the
same (`--device NAME` when the Mac knows several phones, `--team ID` when Xcode has several Apple IDs). The phone app
then connects on the **Phone** page (a code to scan in the app's Lab tab).

## Afterwards

- **Something not right?** `octo doctor` checks the Mac, the software, the models, the login
  items, the lab, and what macOS may refuse without saying (its firewall for the phone, Local Network for the
  recorder, another program on port 8765), and says what to do about anything that isn't ready.
- **Update:** **Octo update ready** in the octopus menu when there is one, or Octo → Update Octo…, or `octo update` (it waits for a running step, takes the new version and restarts the lab). From the
  one-line install, each version gets its own folder and runs only once its install has worked, so a failed update leaves
  the one you had running; the one before is kept, and `octo update --back` goes back to it (unless the newer one has
  already changed the lab's database: it says so, and changes nothing). A copy of the database from before each such
  change is kept for 14 days in `data/backups/pre-schema-<n>.db`. Your data (in `lab.noindex`, or
  `data/` in a clone, including your principles in `data/rubric.md`) is never touched by an update.
- **Uninstall:** in the Octo app, **Octo → Uninstall Octo…**, or in Terminal:

  ```bash
  ~/Library/Application\ Support/octo/app/.venv/bin/octo uninstall
  ```

  (in a clone, `.venv/bin/octo uninstall`). It lists what it will remove, asks once whether to keep your
  conversations (Enter keeps them; type `delete` to delete them too), then removes: the Octo app and its Open at Login,
  the three login items (which stops the lab), the app's preferences, the speech models the lab uses (only those, from
  `~/.cache/huggingface/hub`, which other programs share), the relay's files in `~/Library/Application Support/octo`,
  and the code if the one-line installer put it there. It prints what it removed and what it kept. `--dry-run` only
  lists; `--keep-data` or `--delete-data` answer the question in advance, and `--set-aside` keeps them renamed aside (deleted after 14 days) so a new
  install starts empty. It says when the lab was last exported and how many conversations came since.
  **Kept, and said so:** your conversations unless you chose `delete` (`lab.noindex`, or `data/`, `audio/lab/`,
  `notes/` and `runs/` in a clone: recordings, transcripts, reviews, voiceprints and the recorder's key; copy them
  first if you'll want them later); a folder you cloned yourself (delete it when you like); your Hugging Face sign-in
  (`hf auth logout`); uv in `~/.local/bin` and its Pythons in `~/.local/share/uv`; Ollama and its models; Apple's
  command line tools. Those last are tools other programs may use: delete them yourself if nothing else needs them.

## Export and import: a new Mac, a clean reinstall, a copy kept elsewhere

In the Octo app: **File → Export Lab…** (or **Export…** on the Settings page). It asks for a password (or suggests
one), where to save the file, then shows its progress and what the file holds. **File → Import Lab…** (or **Import…**
on the Settings page, and on the Setup page of a new install) asks for the file and its password, shows what's in it
and what it would replace, and asks before it changes anything. In Terminal, the same:

```bash
~/Library/Application\ Support/octo/app/.venv/bin/octo export
```

Either way it makes one file, `~/Octo exports/Octo export <date> <time>.dmg`: the whole lab (conversations, transcripts,
reviews, people, voiceprints, marks, the coach's notes, your settings, tokens and principles, the recorder's and the
phone's keys, and recordings from the last 60 days). It's macOS's own encrypted disk image, with a password you choose
(at least 14 characters, or press Enter for a five-word one). **Octo doesn't keep the password: without it nobody can
open the file.** Save it in your password manager. The lab stops for a few seconds while the copy is taken, then the
file is checked: every file's checksum, the database, the counts. Left out: the daily backups, playback clips, logs
and bench output.

To bring it in, on a new install (its lab must have no recordings yet):

```bash
~/Library/Application\ Support/octo/app/.venv/bin/octo import ~/Octo\ exports/Octo\ export\ 2026-10-19\ 1430.dmg
```

It shows when the export was made, what it holds and which services its settings send transcripts to, and asks before
it changes anything. Nothing is merged: `--replace` sets a lab with recordings aside (kept 14 days) and brings the
export in. Anything you deleted, forgot or withdrew after the export was made comes back with it.

- **A clean reinstall, deleting nothing:** run `octo export --stop` (the lab stays stopped, so nothing more is pulled
  from the recorder, which keeps what it records on its card), then `octo uninstall --set-aside` (your lab is renamed
  aside, so the new install starts empty), then run the install line, then `octo import …`. The aside folder is
  deleted 14 days after the date in its name.
- **A new Mac:** carry the file across, install, import. Then remove Octo from the old Mac, or both will pull from the
  recorder. The phone may need the Phone page's code scanned again.
- **Where to keep it:** `~/Octo exports/` isn't synced. Desktop and Documents may be (iCloud), so `octo export` asks
  before it writes there. Taking the file off this Mac is your choice and your step.
- **If an export gets out**, it's encrypted, and its password is the whole defence. If you think someone has both:
  - pair the recorder again with a new key: `octo device setup --new-key`, then `octo device flash` over USB;
  - unpair your phones on the Phone page and pair them again;
  - replace your API keys and TickTick token at their services;
  - remember that it holds voiceprints of the people you recorded.
- **If the recorder has been out of your hands** (lost, lent, left somewhere), treat it as untrusted: there's no
  Secure Boot on it, so reflashing is the remedy. Plug it in and run `octo device flash --direct`: it writes the
  bootloader, partition table and firmware through the chip's own loader, not through whatever firmware is on it
  (from a release, the whole flash image, which also clears its settings and pairing; built from source, only those
  parts). Then `octo device pair`, which replaces the key it trusts.

## What leaves your Mac

| | Stays on the Mac | Leaves it |
|---|---|---|
| Audio, voiceprints | always | never |
| Transcripts | stored here | the text of one conversation, when it's reviewed, to the model you chose |
| Your principles and profile | stored here | sent with each review, to the same model |
| API keys | `data/providers.json`, readable by you only | only to the service they belong to |
| An export (`octo export`) | `~/Octo exports/`, encrypted with your password | only where you put it yourself |

Nothing is sent to Octo's makers: there are no accounts and no servers of ours.
