"""`octo` command: the conversation lab from the terminal.

  octo add FILE... [--source phone|bench|device|file]   bring recordings in (any format macOS reads)
  octo process                                          run everything that's pending
  octo sessions                                         conversations found so far
  octo show SESSION [--write]                           the note for one conversation

  octo enroll NAME FILE [--self]    voiceprint from a recording of only that person (--self: you, the lab's owner)
  octo whois FILE                   how close a single-speaker recording is to each voiceprint (checks an enrolment)
  octo label SESSION SPEAKER NAME   "speaker B in this conversation is Amma" (stores her voiceprint)
  octo confirm SESSION              accept all suggested names in a conversation
  octo people                       everyone with a voiceprint
  octo forget NAME                  delete a person's voiceprints and labels
  octo reprint                      after a new voice model: rebuild voiceprints from audio still kept
  octo keep SESSION                 restore a conversation discarded for "your voice not heard" (D10)

  octo compare SESSION [--models a,b]   blind A/B of two local models' insights (plan §4.8)
  octo compare SESSION --reveal         which model was A and which was B
  octo evalinsight [--models a,b]       score models on scripted transcripts with known answers
  octo coach [--show]                   ask the coach across your reviews (one Claude call), or show its last note
  octo gate include|exclude SESSION    count a conversation on Progress, or not
  octo redo SESSION STAGE               run one step of one conversation again (and what follows)
  octo provisional SESSION --reason R | --clear   its review counts only provisionally until cleared (J9)
  octo notify test|now                  a macOS notification: a test, or what newly needs you
  octo login-item install|remove|status start Ollama and the web UI when you log in (per-user)
  octo launcher install|remove          the Octo app in Applications: the lab in its own window, the menu bar
  octo uninstall [--keep-data|--delete-data|--set-aside] [--dry-run]  remove everything Octo put on this Mac
  octo export [PATH] [--stop]           the whole lab in one encrypted file (~/Octo exports/)
  octo import FILE [--replace]          bring an export into this Mac's lab
  octo deploy|update [--now]            update the lab to origin/main and restart it, from any copy
                                        (waits for running steps unless --now; octo/deploy.py)
  octo ticktick setup [--list NAME]     connect TickTick with your personal API token ("What to do" → TickTick)
  octo ticktick status                  whether it's connected, and to which list
  octo invite setup|status|remove       the Gmail account that sends beta invitations (Settings → Invite a tester)
  octo doctor [--json]                  is this Mac ready to run the lab, and what to do if not
  octo open [PAGE]                      open the lab in this Mac's browser, letting the browser in once (the Mac's key)
  octo beta results [--out FILE]        the beta's results: counts only, no names or text (read it before sending)
  octo models [download]                the models the steps need; fetch what's missing (gated ones need `hf auth login`)
  octo owner [show]                     who the reviews and the coach are about (octo/owner.py)
  octo owner set [--name N] [--pronoun he|she|they] [--about A] [--principles-from P] [--never X]
  octo phone install [--device D]       build the iPhone app with this Mac's Xcode and your Apple ID, and install it

  octo device pair [--ssid NAME]    pair a recorder on USB with this Mac (rec-12+), and add a Wi-Fi network
  octo device networks [list | add NAME | remove NAME]   the recorder's Wi-Fi networks (up to five)
  octo device setup [--new-key]     a developer build: write the recorder's secrets.h (Wi-Fi + signing key)
  octo device flash [--direct]      build and update the recorder's firmware: over USB if it's plugged in, else
                                    over Wi-Fi; its screen shows how far, and it goes back if the new one fails
                                    (--direct: the chip's own loader, for a recorder that won't start). A new
                                    board on USB gets Octo's firmware installed; then `octo device pair`. Without
                                    arduino-cli it uses a release's prebuilt firmware (firmware/recorder/release)
  octo device status                what the recorder is doing (over home Wi-Fi)
  octo device on|off                start or stop recording (the device remembers)
  octo device sync                  pull new audio, delete it from the card, join it into recordings
  octo filler status                the live "you know" cue's last 7 days (labelled by the transcript), the
                                    installed model, the last retrain and any better one waiting (B22)
  octo filler train                 retrain the cue on every recorder conversation to date (about an hour; the
                                    lab does it weekly at night); a better one beyond the seed spread waits
  octo filler install [--target T]  put the waiting better cue on the recorder (recording off): as data on rec-27
                                    and later (B29), else built into the firmware ("you know" only)
  octo filler undo                  the recorder's model before back (rec-27 keeps it), with its own threshold
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from . import HOME, NOTES, RATE, ROOT
from . import audio as A
from .calls import channel
from .db import connect, load_json, row, rows, tx
from . import sessions as SV
from .pipeline import add_recording, current, run_pending


def clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def speaker_names(conn, session_id: str) -> dict[str, str]:
    """Letter → display name, as the web page shows it: "Amma", "Amma?", "Rishi or Anil?", "Speaker B"."""
    return {sp["letter"]: SV.display(sp) for sp in SV.speakers(conn, session_id)}


def numbers_table(conn, session_id: str, names: dict[str, str]) -> list[str]:
    """Neutral counts (plan §3.7): no colouring, no "good" or "bad"."""
    run = current(conn, session_id, "numbers")
    if not run or run["status"] != "done":
        return []
    n = load_json(run["output"])
    out = ["", "## Numbers", "",
           "| | Talk | Share | Turns | Longest turn | Words | Questions¹ | Interrupted others² | Was interrupted² | Fillers per 100 words³ |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for spk, v in sorted(n["speakers"].items()):
        out.append(f"| {names.get(spk, spk)} | {clock(v['talk_s'])} | {v['share']:.0%} | {v['turns']} | "
                   f"{clock(v['longest_turn_s'])} | {v['words']} | {v['questions']} | "
                   f"{v['interrupted_others']} | {v['was_interrupted']} | {v.get('fillers_per_100_words', '–')} |")
    out += ["", f"{n['speaker_changes']} changes of speaker. ¹ from Whisper's punctuation, approx. "
            "² started while someone else was talking and they stopped; turn edges are ±0.3 s, approx. "
            "³ um, uh, ah, you know, kind of, sort of: only where the transcription kept them, approx."]
    return out


def insights_section(conn, session_id: str) -> list[str]:
    from .planning import insight_ready

    run = current(conn, session_id, "insight")
    if not run or run["stale"]:
        if not insight_ready(conn, session_id):
            return ["", "## Insights", "", "_Waiting for speakers: confirm which one is you and answer any "
                    "suggested names (`octo confirm` / `octo label`). Insights never run on unconfirmed speakers._"]
        return ["", "## Insights", "", "_Not run yet: `octo process`._"]
    if run["status"] != "done":
        return ["", "## Insights", "", f"_{run['status']}_" + (f": {run['error']}" if run["error"] else "")]
    out = load_json(run["output"])
    return ["", "## Insights", "", f"_{out['model']} · rubric {out['rubric']} · {clock(out['seconds'])}_"] \
        + render_insights(out)


def render_insights(out: dict) -> list[str]:
    lines = []
    if out["skipped"]:
        return lines + ["", f"_Skipped: {out['skipped']}._"]
    say = lambda q: f"- `{clock(q['start'])}` “{q['text']}”" + (" ⚠︎" if q["uncertain"] else "")
    if out.get("kind") == "review":
        return lines + render_review(out, say)
    lines += ["", "### Your conduct"]
    for f in out["conduct"]:
        lines += ["", f"**{f['principle']} · {f['title']}**", "", f["observation"], ""]
        lines += [say(q) for q in f["quotes"]]
        lines.append(f"_{len(f['quotes'])} quote(s) found in the transcript"
                     + (f", {f['unverified_quotes']} not found and dropped" if f["unverified_quotes"] else "")
                     + "".join(f" · {x}" for x in f["flags"]) + "_")
        if f["try_next_time"]:
            lines += ["", f"Try next time: {f['try_next_time']}"]
    if not out["conduct"]:
        lines += ["", "_Nothing in this conversation bore on your principles._"]
    if out["no_moment"]:
        lines += ["", f"_No moment for principles {', '.join(map(str, out['no_moment']))}._"]
    if out["dropped"]:
        lines += [f"_Dropped for lack of evidence in your own words: principles "
                  f"{', '.join(str(d['principle']) for d in out['dropped'])}._"]
    if out["beneath"]:
        lines += ["", "### Beneath the surface", ""]
        for b in out["beneath"]:
            lines.append(f"- {b['question']} (" + ", ".join(f"`{clock(q['start'])}`" for q in b["quotes"]) + ")")
    return lines


def invite_command(args) -> int:
    """The app password and the install line are typed here (hidden) and kept in data/ only: the page never has them,
    and Settings shows Invite a tester only once they're kept."""
    import getpass
    from . import invite as IV
    if args.action == "status":
        st = IV.status()
        print(f"sends from {st['from']}; {len(st['sent'])} invited" if st["ready"] else "not set up: octo invite setup")
        return 0
    if args.action == "remove":
        IV.remove()
        print("Removed (revoke the app password in the Google account too)")
        return 0
    print("The Gmail account invitations come from needs an app password: in that Google account, Security →")
    print("2-Step Verification (turn it on) → App passwords → make one named Octo. Leave a line blank to keep what's kept.")
    try:
        st = IV.setup(input("Sign in as (the Gmail address): "), getpass.getpass("App password (hidden): "),
                      getpass.getpass("The install line testers paste, from beta_release.sh (hidden): "),
                      input("Send as (an alias in that Gmail's Settings → Accounts → Send mail as; blank: "
                            f"{IV.load().get('as') or 'the same address'}): "))
    except IV.InviteError as e:
        print(e)
        return 1
    print(f"Gmail accepted it. Settings → Invite a tester now sends from {st['from']}. Kept in {IV.CONFIG} (only you can read it).")
    return 0


def ticktick_command(args) -> int:
    """The token is typed by the owner here (hidden), checked against TickTick, and kept in data/ only."""
    import getpass
    from . import ticktick as TT
    if args.action == "status":
        st = TT.status()
        print(f"connected; tasks go to {st['list']}" if st["connected"] else "not connected: octo ticktick setup")
        return 0
    if args.action == "remove":
        TT.disconnect()
        print("TickTick disconnected (the token is deleted from this Mac; revoke it in TickTick too)")
        return 0
    print("In TickTick on the web: your avatar → Settings → Account → API Token. Create one and paste it here.")
    print("(Or paste it on the Settings page of the lab.)")
    try:
        st = TT.connect(getpass.getpass("TickTick API token (hidden): "), args.list)
    except TT.TickTickError as e:
        print(e)
        return 1
    print(f"Connected. Tasks go to {st['list']}, tagged '{TT.TAG}', due dates in {st['timezone']}. "
          f"The token is in {TT.CONFIG} (only you can read it).")
    return 0


def render_review(out: dict, say) -> list[str]:
    """A Claude review (octo/review_any.py), in the order the 20:31 reference reads."""
    lines = ["", "### What happened", "", out["summary"]]
    for title, key, head in (("Reading the others", "reading", lambda r: f"{r['person']}: {r['headline']}"),
                             ("Between the lines", "between", lambda b: b["headline"]),
                             ("Against your principles", "conduct", lambda f: f"{f['principle']} · {f['title']}")):
        if out[key]:
            lines += ["", f"### {title}"]
        for it in out[key]:
            lines += ["", f"**{head(it)}**", "", it["observation"], ""] + [say(q) for q in it["quotes"]]
            if it["unverified_quotes"] or it["flags"]:
                lines.append("_" + "; ".join(([f"{it['unverified_quotes']} quote(s) not found, dropped"] if it["unverified_quotes"] else [])
                                             + it["flags"]) + "_")
    if out["no_moment"]:
        lines += ["", f"_No moment for principles {', '.join(map(str, out['no_moment']))}._"]
    if out["next"]:
        lines += ["", "### What to do", ""]
        for i, n in enumerate(out["next"], 1):
            title = n.get("title") or n.get("action", "")
            due = f" _(by {n['due']})_" if n.get("due") else ""
            lines.append(f"{i}. **{title}**{due} " + " ".join(x for x in (n.get("detail"), n.get("why") and f"Why: {n['why']}") if x))
    if out["limits"]:
        lines += ["", f"_Limits: {out['limits']}_"]
    if out["dropped"]:
        lines += ["", "_Dropped for lack of evidence: " + "; ".join(
            f"{d.get('headline') or 'principle ' + str(d.get('principle'))} ({d['reason']})" for d in out["dropped"]) + "._"]
    return lines


def note(conn, session_id: str) -> str:
    sess = next(iter(rows(conn, "SELECT * FROM sessions WHERE id=?", session_id)), None)
    if not sess:
        raise SystemExit(f"no session {session_id}")
    rec = rows(conn, "SELECT * FROM recordings WHERE id=?", sess["recording_id"])[0]
    tr = current(conn, session_id, "transcribe")
    lines = [
        f"# Conversation {session_id}",
        "",
        f"- Recording: `{rec['id']}` ({rec['source']}), {clock(sess['start_s'])}–{clock(sess['end_s'])} "
        f"into it · {clock(sess['end_s'] - sess['start_s'])} long · {sess['speech_s']:.0f} s of speech",
    ]
    if not tr or tr["status"] != "done":
        lines += ["", "_Not transcribed yet: run `octo process`._"]
        return "\n".join(lines) + "\n"
    out = load_json(tr["output"])
    _, dz_out, _ = SV.diarized(conn, session_id)  # with the owner's corrections, as on the web page
    lines.append(f"- Transcribed by `{out['model'].split('/')[-1]}`, language detected: {out['language']}"
                 + (" · **stale: inputs changed, run `octo process`**" if tr["stale"] else ""))
    paused = SV.paused(tr)
    if paused:
        lines += [f"- {paused}: English only for now, so no transcript or insights (speakers and talk time stay)."]
        dz_out = {**dz_out, "lines": []} if dz_out else None
    if dz_out:
        names = speaker_names(conn, session_id)
        total = sum(dz_out["talk_s"].values()) or 1
        share = " · ".join(f"{names.get(k, k)} {v / total:.0%}" for k, v in sorted(dz_out["talk_s"].items()))
        lines += [f"- Speakers: {len(dz_out['speakers'])} ({share} of talk time, approx.)"]
        lines += numbers_table(conn, session_id, names)
        if not paused:
            lines += insights_section(conn, session_id)
        lines += ["", "## Transcript", ""]
        for u in dz_out["lines"]:
            flag = " ⚠︎" if u["uncertain"] else ""
            lines.append(f"`{clock(u['start'])}` **{names.get(u['speaker'], u['speaker']) or '?'}:** {u['text']}{flag}")
        lines += ["", "_⚠︎ = uncertain speaker or words: insights won't lean on these lines._",
                  "_A name with ? is a suggestion from voiceprints: `octo confirm` or `octo label` to settle it._"]
    else:
        lines += ["", "_Speakers not separated yet: run `octo process`._", "", "## Transcript", ""]
        for s in out["segments"]:
            flag = " ⚠︎" if s.get("avg_logprob", 0.0) < -0.8 or s.get("no_speech_prob", 0.0) > 0.6 else ""
            lines.append(f"`{clock(s['start'])}` {s['text']}{flag}")
        lines += ["", "_⚠︎ = low confidence: may be misheard or not speech._"]
    if out.get("rejected_as_silence"):
        dropped = "; ".join(f"`{clock(r['start'])}` “{r['text']}”" for r in out["rejected_as_silence"])
        lines += [f"_Dropped as invented in silence: {dropped}_"]
    return "\n".join(lines) + "\n"


def _firmware(D) -> tuple[bytes, Path, bool]:
    """The app image (for updates), the whole flash image (for a first install), and whether they were built here:
    built when the Arduino toolchain is installed, as before; otherwise a release's prebuilt ones (open-source plan
    I5), checked against its SHA256SUMS."""
    import shutil
    from bench.firmware import BUILD, RECORDER, compile_sketch
    if not shutil.which("arduino-cli"):
        return D.release_firmware("recorder.ino.bin").read_bytes(), D.release_firmware("recorder.ino.merged.bin"), False
    if D.WIFI_SECRETS.exists():  # a developer build: secrets.h again, same keys (the encryption key once, M41)
        D.setup()
    D.filler_model()  # the owner's filler model into the build, if the lab has one (B20)
    compile_sketch(RECORDER, lambda line: None)
    out = BUILD / RECORDER
    return (out / f"{RECORDER}.ino.bin").read_bytes(), out / f"{RECORDER}.ino.merged.bin", True


def _flash(D, args) -> int:
    """`octo device flash`: the firmware (built, or a release's), then an update over USB or Wi-Fi; on a board that
    has never run Octo, a first install over USB (called holding the recorder)."""
    from bench.device import BoardError, connect, find_port
    from bench.firmware import RECORDER, upload
    paired, on_usb = D.load_config() is not None, find_port()
    if not paired and not on_usb:
        print("no recorder paired with this Mac yet: plug it in with a USB-C data cable and run `octo device flash` "
              "again to install Octo's firmware, then `octo device pair` (a developer build: `octo device setup`)")
        return 1
    try:
        image, whole, built = _firmware(D)
    except (BoardError, D.DeviceError) as e:
        print(f"{e}" + (f" -- {e.hint}" if getattr(e, "hint", None) else ""))
        return 1
    if not paired:  # a new board: the whole image with the chip's loader, then pairing gives it its keys
        try:
            D.install_firmware(on_usb, whole)
        except D.DeviceError as e:
            print(f"not installed: {e}")
            return 1
        print(f"Octo's firmware is on the recorder ({on_usb}). Next, pair it with this Mac: octo device pair")
        return 0
    if on_usb or args.usb or args.direct:
        if on_usb and not args.direct:
            # Through its own update code (rec-13): the progress and the result on its screen, and the
            # firmware before to go back to.
            try:
                board = connect("recorder")
            except BoardError:
                print("the recorder isn't answering on USB: flashing it directly instead")
            else:
                with board:
                    try:
                        st = D.update_over_usb(board, image)
                        print(f"recorder updated over USB: now {st.get('fw')}" + (", back on Wi-Fi" if st.get("wifi") else ""))
                        return 0
                    except D.OldFirmware as e:
                        print(f"{e}: flashing it directly this time, so its screen won't show it")
                    except (D.DeviceError, BoardError) as e:
                        print(f"not updated: {e}")
                        return 1
        # Directly, with the chip's own loader (esptool): nothing of ours runs during it, so the screen
        # shows nothing and there's no going back. For older firmware, or a recorder that won't start.
        try:
            if built:
                port = upload(RECORDER, lambda line: None, lambda pct: None)
            else:
                D.install_firmware(on_usb or "", whole)
                port = on_usb
        except D.DeviceError as e:
            print(f"not updated: {e}")
            return 1
        except BoardError as e:
            print(f"{e}" + (f" -- {e.hint}" if e.hint else ""))
            return 1
        print(f"recorder flashed directly on {port} (USB)")
        return 0
    # Not on USB: over the home Wi-Fi, signed, if its firmware can take it.
    try:
        st = D.update_over_wifi(D.Device.from_config(), image)
    except D.DeviceError as e:
        print(f"not updated: {e}")
        return 1
    want = D.firmware_version()
    if st.get("fw") == want and not st.get("went_back") and st.get("pending"):
        # Started, but rec-16 keeps it only once it works (review 8 Oct, finding 13).
        print(f"not kept: {want} started, but its {D._fault(st)}; the recorder goes back to the firmware before "
              "within 10 minutes")
        return 1
    if st.get("fw") == want and not st.get("went_back"):
        print(f"recorder updated over Wi-Fi: now {want}")
        return 0
    # The new firmware didn't start properly and the bootloader went back (rec-10 says so on screen).
    print(f"not updated: the recorder rolled back to {st.get('fw')} (expected {want})")
    return 1


def device_command(conn, args) -> int:
    from . import device as D

    if args.action in ("pair", "networks"):
        import getpass
        from bench.device import BoardError, connect
        try:
            if args.action == "pair":
                if D.developer_build():
                    print(f"note: {D.SECRETS_H.relative_to(ROOT)} exists, so a recorder built here takes its key and "
                          "network from it; pairing still stores them on the recorder")
                with connect("recorder") as board:
                    st = D.pair_usb(board, args.new_key)
                    names = D.networks_usb(board, "list")
                    if args.ssid:
                        names = D.networks_usb(board, D.net_command("add", args.ssid, getpass.getpass(f"Password for {args.ssid} (blank if open): ")))
                print(f"paired with this Mac ({st.get('fw')}); networks: {', '.join(names) or 'none yet: octo device networks add NAME'}")
                return 0
            op = (args.rest or ["list"])[0]
            name = " ".join(args.rest[1:])
            if op != "list" and not name:
                print("give the network's name: octo device networks add NAME")
                return 2
            password = getpass.getpass(f"Password for {name} (blank if open): ") if op == "add" else ""
            with D.holding(conn, wait_s=30):
                names = D.networks(D.net_command(op, name, password))
            print("networks, tried in this order: " + (", ".join(names) or "none"))
            return 0
        except (D.DeviceError, BoardError) as e:
            print(f"{e}" + (f" -- {e.hint}" if getattr(e, "hint", None) else ""))
            return 1

    if args.action == "setup":
        made = D.setup(args.new_key)
        print(f"wrote {D.SECRETS_H.relative_to(ROOT)} and {D.CONFIG} (Wi-Fi from the bench's 03_wifi; "
              + ("new signing key): flash firmware/recorder now" if made else "same key as before)"))
        return 0
    if args.action == "flash":
        try:
            # Review 8 Oct, finding 7: the recorder has one nonce for this Mac; the lab's sync and status checks
            # wait while an update has it (an update died at 78% on 8 Oct, racing them).
            with D.holding(conn, wait_s=120):
                return _flash(D, args)
        except D.Busy as e:
            print(f"not updated: {e}")
            return 1
    if args.action == "assemble":  # recordings from chunks already on the Mac, recorder or not
        made = D.assemble(conn, {(r["boot"], r["seg"]) for r in rows(
            conn, "SELECT DISTINCT boot, seg FROM device_chunks WHERE recording_id IS NULL")})
        print(f"{len(made)} new recording(s)" + ("; next: octo process (or leave it to the web UI)" if made else ""))
        return 0
    try:
        dev = D.Device.from_config()
        if args.action in ("on", "off"):
            with D.holding(conn, wait_s=30):
                st = D.set_recording(dev, args.action == "on")
        elif args.action == "filler":  # octo device filler on|off [THRESHOLD] (B20)
            if not args.rest or args.rest[0] not in ("on", "off"):
                print("usage: octo device filler on|off [THRESHOLD, 0.5-0.999]")
                return 1
            try:
                st = D.set_filler(dev, args.rest[0] == "on", float(args.rest[1]) if len(args.rest) > 1 else None)
            except ValueError as e:
                print(e)
                return 1
            if not st.get("filler_model"):
                print("this recorder's firmware has no filler model: put it in the lab's data/filler/ and `octo device flash`")
                return 1
            print(f"filler cue {'on' if st['filler_on'] else 'off'}, threshold {st['filler_threshold']}"
                  + ("" if st.get("filler_ready") else " (not running: no memory for it on the recorder)"))
        elif args.action == "status":
            st = dev.call("/status")
        else:
            r = D.sync(conn, dev, speech=D.lazy_speech())
            print(f"pulled {r['pulled']} chunks, deleted {r['deleted_on_device']} from the card"
                  + (f", {r['bad']} didn't match their checksum (left on the card)" if r["bad"] else "")
                  + f", {len(r['recordings'])} new recordings")
            st = r["status"]
            if r["recordings"]:
                print("next: octo process (or leave it to the web UI)")
    except D.DeviceError as e:
        print(e)
        return 1
    D.save_status(st)
    free_min = st["card_free"] / (RATE * 2) / 60 if st["card_free"] else 0
    print(f"{st['state']} · boot {st['boot']} · up {st['uptime_ms'] / 3.6e6:.1f} h · card {free_min / 60:.0f} h left · "
          f"Wi-Fi {st['rssi']} dBm · lost samples {st['dropped_samples']} · worst SD write {st['worst_write_ms']} ms"
          # Card errors and the phone's link, for the rate of card errors since rec-14 (learnings, 9 Oct)
          + (f" · SD errors {st['sd_failures']} (last {st.get('last_sd_error')} after {st.get('last_sd_error_took_ms')} ms)"
             if st.get("sd_failures") else "")
          + (" · phone connected" if st.get("ble_connected") else "")
          + (f" · error {st['error']}" if st["error"] else ""))
    return 0


def coach_text(v: dict) -> str:
    """A coach note for the terminal."""
    from .coach import focus_text
    from .measures import LABEL
    n = v["note"]
    when = lambda ts: datetime.fromtimestamp(ts).strftime("%a %d %b %H:%M")
    read = v["read"] or {}
    out = [f"The coach · {when(v['created_at'])} · read {read.get('conversations', 0)} conversations"
           f" ({read.get('answered', 0)} you've answered)"]

    def moments(t: dict) -> list[str]:
        lines = []
        for e in t["evidence"]:
            q = f" \"{e['quote']['text']}\"" if e.get("quote") else ""
            lines.append(f"    {when(e['at'])} · {e['label']}: {e['headline']}{q}  (octo show {e['session_id']})")
        return lines

    if n["work_on"]:
        w = n["work_on"]
        out += ["", f"WORK ON: {w['theme']}", f"  {w['why']}", f"  Seen in {w['conversations']} conversations:", *moments(w)]
        if w["try"]:
            out.append(f"  TRY: {focus_text(w)}")
        k = w["know"]
        out.append(f"  YOU'LL KNOW: {k['how']}" + (f" ({LABEL[k['measure']]})" if k["measure"] in LABEL else ""))
    else:
        out += ["", "Nothing to work on held up across two conversations yet."]
    if n["keep"]:
        out += ["", f"KEEP: {n['keep']['theme']}", f"  {n['keep']['why']}", *moments(n["keep"])]
    for a in n["also"]:
        out += ["", f"ALSO: {a['theme']}", f"  {a['why']}", *moments(a)]
    if n["changed"]:
        out += ["", f"SINCE LAST TIME: {n['changed']}"]
    if n["limits"]:
        out += ["", f"CAN'T TELL YET: {n['limits']}"]
    for d in n["dropped"]:
        out.append(f"(dropped, {d['reason']}: {d['theme']})")
    return "\n".join(out)


def coach_command(conn, args) -> int:
    from . import coach as CO
    if args.run:  # the web app's background call: the note already exists
        try:
            CO.run(conn, args.run)
        except Exception as e:
            print(f"failed: {e}")
            return 1
        return 0
    if args.show:
        n = row(conn, "SELECT * FROM coach_notes WHERE status='done' ORDER BY created_at DESC LIMIT 1")
        print(coach_text(CO.view(conn, n)) if n else "no note yet: octo coach")
        return 0
    try:
        note_id = CO.start(conn)
    except CO.CoachError as e:
        print(f"can't ask now: {e}")
        return 1
    print("reading your reviews (one Claude call; a few minutes)…", flush=True)
    try:
        n = CO.run(conn, note_id)
    except Exception as e:
        print(f"failed: {e}")
        return 1
    print(coach_text(CO.view(conn, n)))
    return 0


def filler_command(args) -> int:
    """`octo filler status|train|install|undo|cancel` (B22, B29: octo/filler_loop.py)."""
    from . import filler_loop as FLP
    from . import fillers as FI
    if args.action == "train":
        target = FI.normalize(args.target) if args.target else FLP.installed_target()
        if not FLP.hold_job(target):
            print(f"not started: “{FLP.job()['target']}” is training (one at a time; `octo filler cancel` stops it)")
            return 1
        try:
            st = FLP.train(target=target)
        finally:
            FLP.JOB.unlink(missing_ok=True)
        return 1 if st.get("error") else 0
    if args.action == "cancel":
        print("cancelled" if FLP.cancel_job() else "nothing is training")
        return 0
    if args.action in ("install", "undo"):
        try:
            res = FLP.undo() if args.action == "undo" else FLP.install(target=FI.normalize(args.target) if args.target else None)
        except FLP.InstallRefused as e:
            print(f"not done: {e}")
            return 1
        except Exception as e:
            print(f"not done (the model before is still on): {e}")
            return 1
        print(f"the recorder's cue now runs the model for “{res['target']}”"
              + (f" at {res['threshold']}" if res.get("threshold") else "") + (f"; {res['error']}" if res.get("error") else ""))
        return 0
    lv = FLP.live(connect())
    print(f"last {lv['days']} days: {lv['cues']} cues in {lv['conversations']} conversations, {lv['right']} right; "
          f"{lv['caught']} of your {lv['his']} “{FLP.installed_target()}”s caught while it was on"
          + (f"; {lv['waiting']} not transcribed yet" if lv["waiting"] else ""))
    s = FLP.state()
    if s["train"]:
        t = s["train"]
        print("retrain: " + ("running" if t.get("running") else time.strftime("%d %b %H:%M", time.localtime(t["started_at"])))
              + (f", failed: {t['error']}" if t.get("error") else "")
              + (f", installed data {t['bench']['before_mean']:.0%} vs all {t['bench']['now_mean']:.0%}" if t.get("bench") else ""))
    if s["candidate"]:
        print("a better cue is waiting: " + FLP.better_than_installed(s["candidate"]) + ". `octo filler install`")
    for r in FLP.readiness(connect()):
        print(f"  {r['target']}: {r['his']} of yours, {r['others']} of others'"
              + (f", you say it {r['leans']}x as often" if r["leans"] else "") + f" · {r['status']}"
              + (f", about {r['weeks_left']} weeks to {r['need']}" if r["weeks_left"] else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="octo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add")
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--source", default="file", choices=["phone", "bench", "device", "file", "call"])
    p.add_argument("--speakers", action="store_true", help="a call heard on the Mac's speakers: take the others out of the mic")
    sub.add_parser("process")
    sub.add_parser("compact-audio", help="store WAV recordings as FLAC (lossless, about a third of the space)")
    p = sub.add_parser("retry", help="run failed steps again now (all, or one conversation)")
    p.add_argument("session", nargs="?")
    sub.add_parser("sessions")
    p = sub.add_parser("show")
    p.add_argument("session")
    p.add_argument("--write", action="store_true", help=f"also save to {NOTES.name}/")
    p = sub.add_parser("enroll")
    p.add_argument("name")
    p.add_argument("file", type=Path)
    p.add_argument("--self", action="store_true", help="this is you, the lab's owner")
    p = sub.add_parser("whois")
    p.add_argument("file", type=Path)
    p = sub.add_parser("label")
    p.add_argument("session")
    p.add_argument("speaker")
    p.add_argument("name")
    p.add_argument("--print-only", action="store_true",
                   help="the answer is saved already (the web page does that first): only add the voiceprint, if it still stands")
    p = sub.add_parser("confirm")
    p.add_argument("session")
    sub.add_parser("people")
    sub.add_parser("reprint")
    p = sub.add_parser("forget")
    p.add_argument("name")
    p = sub.add_parser("keep")
    p.add_argument("session")
    p = sub.add_parser("redo", help="run one step of one conversation again (and what follows from it)")
    p.add_argument("session")
    p.add_argument("stage")
    p = sub.add_parser("parts", help="group earlier conversations by the lab's one-discussion rule (B35); lists first")
    p.add_argument("--apply", action="store_true", help="group them (without it, only list what would be grouped)")
    p = sub.add_parser("provisional", help="mark a review as counting only provisionally (J9), or clear it")
    p.add_argument("session")
    p.add_argument("--reason")
    p.add_argument("--clear", action="store_true")
    p = sub.add_parser("coach", help="what your reviews say across conversations: one Claude call (octo/coach.py)")
    p.add_argument("--show", action="store_true", help="the last note, without asking again")
    p.add_argument("--run", metavar="NOTE", help=argparse.SUPPRESS)  # the web app's background call
    p = sub.add_parser("gate")
    p.add_argument("action", choices=["include", "exclude"])
    p.add_argument("session")
    p = sub.add_parser("ticktick")
    p.add_argument("action", choices=["setup", "status", "remove"])
    p.add_argument("--list", help="setup: the TickTick list for tasks (default: Inbox)")
    p = sub.add_parser("invite", help="the Gmail account that sends beta invitations")
    p.add_argument("action", choices=["setup", "status", "remove"])
    p = sub.add_parser("beta", help="the beta's results file: counts only")
    p.add_argument("action", choices=["results"])
    p.add_argument("--out", type=Path)
    p = sub.add_parser("open", help="open the lab in this Mac's browser and let the browser in")
    p.add_argument("page", nargs="?", default="", help="a page, e.g. phone or coach")
    p = sub.add_parser("doctor", help="is this Mac ready to run the lab")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fresh", action="store_true", help="just installed: leave out the models and the setup steps")
    p = sub.add_parser("models", help="the models the steps need")
    p.add_argument("action", nargs="?", default="check", choices=["check", "download"])
    p = sub.add_parser("owner", help="who the reviews and the coach are about")
    p.add_argument("action", nargs="?", default="show", choices=["show", "set"])
    for f in ("name", "pronoun", "about", "principles-from", "never"):
        p.add_argument(f"--{f}")
    p = sub.add_parser("login-item")
    p.add_argument("action", choices=["install", "remove", "status"])
    p = sub.add_parser("launcher", help="the Octo app in Applications: the lab in its own window, the menu bar")
    p.add_argument("action", choices=["install", "remove"])
    p = sub.add_parser("uninstall", help="remove everything Octo put on this Mac; asks whether to keep the conversations")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--keep-data", dest="keep_data", action="store_true", default=None, help="keep them, without asking")
    g.add_argument("--delete-data", dest="keep_data", action="store_false", help="delete them too, without asking")
    g.add_argument("--set-aside", dest="set_aside", action="store_true",
                   help="keep them, renamed aside, so a new install starts with an empty lab (deleted after 14 days)")
    p.add_argument("--dry-run", action="store_true", help="only list what would be removed and kept")
    p.add_argument("--from-app", action="store_true", help=argparse.SUPPRESS)  # the Octo app's Uninstall Octo…
    p = sub.add_parser("export", help="the whole lab in one encrypted disk image, checked once it's made")
    p.add_argument("path", nargs="?", help="a folder or a .dmg file (default: ~/Octo exports/)")
    p.add_argument("--stop", action="store_true", help="leave the lab stopped after (before an uninstall or a move)")
    p.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--yes", action="store_true", help=argparse.SUPPRESS)  # the Octo app asked about synced folders
    p = sub.add_parser("import", help="bring an export into this Mac's lab (only an empty one, unless --replace)")
    p.add_argument("file")
    p.add_argument("--replace", action="store_true", help="set this lab aside first (kept 14 days); nothing is merged")
    p.add_argument("--yes", action="store_true", help="don't ask before bringing it in")
    p.add_argument("--preview", action="store_true", help="only say what's in it and what it would replace")
    p.add_argument("--password-stdin", action="store_true", help=argparse.SUPPRESS)
    p = sub.add_parser("deploy", aliases=["update"], help="update the lab to origin/main and restart it (a beta clone too)")
    p.add_argument("--now", action="store_true", help="restart without waiting for running steps")
    p = sub.add_parser("notify", help="macOS notifications: `test` posts one; `now` checks what needs you")
    p.add_argument("action", choices=["test", "now"])
    p = sub.add_parser("device")
    p.add_argument("action", choices=["pair", "networks", "setup", "flash", "status", "on", "off", "filler", "sync", "assemble"])
    p.add_argument("rest", nargs="*", help="networks: list | add NAME | remove NAME; filler: on|off [THRESHOLD]")
    p.add_argument("--ssid", help="pair: also add this Wi-Fi network (asks for its password)")
    p.add_argument("--new-key", action="store_true", help="setup: replace the signing key (reflash needed)")
    p.add_argument("--usb", action="store_true", help="flash: over USB even if the recorder is reachable on Wi-Fi")
    p.add_argument("--direct", action="store_true",
                   help="flash: over USB with the chip's own loader (esptool), not the recorder's update: nothing on "
                        "its screen and no going back; for a recorder that won't start")
    p = sub.add_parser("filler", help="the recorder's filler cue: status, train, install, undo, cancel (B22, B29)")
    p.add_argument("action", choices=["status", "train", "install", "undo", "cancel"])
    p.add_argument("--target", help="train, install: a filler from your list, or \"uh\" for the hesitations (default: the "
                                    "one on the recorder)")
    p = sub.add_parser("phone", help="build the Octo iPhone app with this Mac's Xcode and install it (octo/iphone.py)")
    p.add_argument("action", choices=["install"])
    p.add_argument("--device", help="the iPhone's name or identifier, when this Mac knows several")
    p.add_argument("--team", help="the Apple ID team to sign with, when Xcode knows several")
    p.add_argument("--bundle-id", help="default app.octo.recorder.<team>; the first lab's is app.octo.recorder")
    p = sub.add_parser("evalinsight")
    p.add_argument("--models", default="qwen3:8b,gemma3:12b")
    p = sub.add_parser("compare")
    p.add_argument("session")
    p.add_argument("--models", default="qwen3:8b,gemma3:12b")
    p.add_argument("--reveal", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "launcher":  # no database needed
        from . import launcher as LN
        if args.action == "install":
            app = LN.install()
            print(f"made {app}: open it (or find Octo in Spotlight) " +
                  ("for the lab in its own window" if LN.native(app) else "to let your browser in"))
        else:
            print("removed the Octo app" if LN.remove() else "there was no Octo app")
        return 0

    if args.cmd == "uninstall":  # no database opened: it reads the lab's without writing
        from . import uninstall as U
        return U.main(args.keep_data, dry_run=args.dry_run, from_app=args.from_app, set_aside=args.set_aside)

    if args.cmd in ("export", "import"):  # never in the server (docs/export-plan.md); it opens the database itself
        from . import export as EX
        pw = EX.stdin_password() if args.password_stdin else None
        try:
            if args.cmd == "export":
                EX.export(args.path, keep_stopped=args.stop, password=pw, yes=args.yes)
                return 0
            if args.preview:  # one JSON line, for the Octo app
                import getpass
                import json
                print(json.dumps(EX.preview(args.file, pw if pw is not None else getpass.getpass("Password: "))))
                return 0
            return EX.import_lab(args.file, replace=args.replace, password=pw, yes=args.yes)
        except EX.ExportError as e:
            print(f"octo {args.cmd}: {e}")
            return 1
        except KeyboardInterrupt:
            print(f"\nocto {args.cmd} stopped; nothing was changed")
            return 1

    if args.cmd == "open":  # no database needed; the link holds the key, so it's opened, never printed
        import subprocess
        from . import mackey as MK
        page = args.page.strip("/#")
        subprocess.run(["open", MK.let_in_url(to=f"/#/{page}" if page else "/")], check=False)
        print("opened the lab in your browser" + (f" at #/{page}" if page else ""))
        return 0
    if args.cmd in ("deploy", "update"):  # the lab's own copy, not this one: no database here
        from . import deploy as DP
        try:
            return DP.deploy(now=args.now)
        except DP.DeployError as e:
            print(f"not deployed: {e}", file=sys.stderr)
            return 1
    if args.cmd == "filler":
        return filler_command(args)
    if args.cmd == "phone":  # no database: it builds from this copy's mobile/
        from . import iphone as IP
        try:
            got = IP.install(args.device, args.team, args.bundle_id)
        except IP.PhoneError as e:
            print(f"octo phone install: {e}", file=sys.stderr)
            return 1
        print(f"Installed on {got['phone']}. It opens until {got['expires']:%d %b %H:%M} UTC: run `octo phone install` again "
              "before then.\nThe first time, on the phone: Settings → General → VPN & Device Management → Developer App → "
              "your Apple ID → Trust.")
        return 0
    conn = connect()

    if args.cmd in ("enroll", "whois", "label", "confirm", "people", "forget", "keep"):
        return people_commands(conn, args)

    if args.cmd == "compare":
        return compare(conn, args)

    if args.cmd == "beta":
        import json as _json

        from . import beta as B
        text = _json.dumps(B.results(conn), indent=2)
        print(text)
        if args.out:
            args.out.write_text(text + "\n")
            print(f"\nsaved to {args.out}: read it, then send it to the person running the beta")
        return 0

    if args.cmd == "doctor":
        from . import doctor as DR
        checks = DR.run(conn, fresh=args.fresh)
        print(DR.as_json(checks) if args.json else DR.report(checks, args.fresh), end="" if not args.json else "\n")
        return 1 if any(c.mark == DR.FAIL for c in checks) else 0

    if args.cmd == "models":
        from . import models as MD
        todo = MD.status(conn)
        for m in todo:
            print(f"{'✓' if m['cached'] else '·'} {m['repo']}{'  (gated)' if m['gated'] else ''}")
        if args.action == "check":
            return 0 if all(m["cached"] for m in todo) else 1
        failed = 0
        for m in [m for m in todo if not m["cached"]]:
            print(f"downloading {m['repo']}…", flush=True)
            got = MD.download(m["repo"])
            if got.get("ok"):
                print("  done")
                continue
            failed += 1
            print("  " + (MD.gated_help(m["repo"]) if got.get("gated") else f"failed: {got.get('error')}"))
        return 1 if failed else 0

    if args.cmd == "owner":
        from . import owner as O
        prof = O.load(conn)
        if args.action == "set":
            given = {k: getattr(args, k) for k in ("name", "pronoun", "about", "principles_from", "never")
                     if getattr(args, k) is not None}
            try:
                prof = O.save(conn, {**prof, **given})
            except ValueError as e:
                print(e)
                return 2
        for k in O.FIELDS:
            print(f"{k:16} {prof.get(k, '—')}")
        return 0

    if args.cmd == "reprint":
        from . import voice as V
        r = V.reprint(conn, HOME)
        print(f"rebuilt {len(r['rebuilt'])} voiceprints with {V.EMBEDDING_MODEL}"
              + (f"; {len(r['missing'])} couldn't be (their audio is gone): confirm those people in new conversations"
                 if r["missing"] else ""))
        return 0

    if args.cmd == "compact-audio":
        from .pipeline import compact_audio
        r = compact_audio(conn)
        if not r["busy"]:
            print(f"{r['converted']} recordings now FLAC: {r['wav_bytes'] / 1e6:.0f} MB → {r['flac_bytes'] / 1e6:.0f} MB"
                  + (f"; {r['failed']} kept as WAV" if r["failed"] else ""))
        return 1 if r["busy"] or r["failed"] else 0

    if args.cmd == "retry":
        from .pipeline import retry
        n = retry(conn, args.session)
        print(f"{n} failed run{'s' if n != 1 else ''} will be tried again: octo process" if n else "nothing failed")
        return 0

    if args.cmd == "redo":
        # Marks the current run out of date: the planner queues it again with the same inputs, and
        # every later step follows. A rated review is kept, with "Redo the review" on its page.
        n = conn.execute("UPDATE stage_runs SET stale=1 WHERE target=? AND stage=? AND is_current=1",
                         (args.session, args.stage)).rowcount
        print(f"{args.session}: {args.stage} will run again on the next pass" if n else "no current run of that step")
        return 0 if n else 1

    if args.cmd == "parts":
        import datetime as dt

        from . import parts as PT
        from .db import tx
        with tx(conn):
            found = PT.sweep(conn, apply=args.apply)
        for f in found:
            a, b, m = f["a"], f["b"], f["m"]
            when = lambda s: dt.datetime.fromtimestamp(s).strftime("%a %d %b %H:%M")  # noqa: E731
            done = ", ".join(x for x, s in (("first", a), ("second", b)) if PT.answered(conn, s["id"])) or "neither"
            print(f"{when(a['at'])} + {when(b['at'])[-5:]}  {f['why']:<10} gap {b['at'] - a['end_at']:.0f} s  "
                  f"{(a['end_s'] - a['start_s']) / 60:.1f} + {(b['end_s'] - b['start_s']) / 60:.1f} min  "
                  f"carry {m['ab']:.2f}/{m['ba']:.2f}  answered: {done}  ({a['id']} + {b['id']})")
        verb = "grouped" if args.apply else "would be grouped (run again with --apply)"
        print(f"{len(found)} pair{'s' if len(found) != 1 else ''} {verb}")
        return 0

    if args.cmd == "provisional":
        if not args.clear and not args.reason:
            print("give --reason (what to check before it counts in full) or --clear")
            return 2
        n = conn.execute("UPDATE sessions SET provisional=? WHERE id=?", (None if args.clear else args.reason, args.session)).rowcount
        print(f"{args.session}: {'counts in full' if args.clear else 'provisional: ' + args.reason}" if n else "no such session")
        return 0 if n else 1

    if args.cmd == "coach":
        return coach_command(conn, args)

    if args.cmd == "gate":
        n = conn.execute("UPDATE sessions SET in_gate=? WHERE id=?", (int(args.action == "include"), args.session)).rowcount
        print(f"{args.session}: {'counted' if args.action == 'include' else 'not counted'}" if n else "no such session")
        return 0 if n else 1

    if args.cmd == "ticktick":
        return ticktick_command(args)

    if args.cmd == "invite":
        return invite_command(args)

    if args.cmd == "login-item":
        from . import login_item as L
        if args.action == "status":
            for label, on in L.status().items():
                print(f"{label:16} {'installed' if on else 'not installed'}")
        else:
            done = L.install() if args.action == "install" else L.remove()
            for label in done:
                print(f"{'installed' if args.action == 'install' else 'removed'} {label}")
        return 0

    if args.cmd == "notify":
        from . import notify as N
        if args.action == "test":
            N.post("Notifications from the lab work · counts only, never what was said")
            print("posted; if nothing showed, allow notifications for Script Editor in System Settings")
        else:
            print(N.check(conn) or "nothing new needs you")
        return 0

    if args.cmd == "device":
        return device_command(conn, args)

    if args.cmd == "evalinsight":
        from .insight_eval import evaluate, report
        evals = [evaluate(m, on_progress=lambda msg: print(f"  {msg}", flush=True)) for m in args.models.split(",")]
        NOTES.mkdir(exist_ok=True)
        path = NOTES / f"insight_eval_{time.strftime('%Y%m%d_%H%M')}.md"
        path.write_text(report(evals))
        print(report(evals).split("\n\n## ")[0])
        print(f"details: {path}")
        return 0

    if args.cmd == "add":
        from .pipeline import DuplicateRecording
        for f in args.files:
            try:
                rec_id = add_recording(conn, f, args.source, started_at=A.recorded_at(f) or f.stat().st_mtime,
                                       speakers=args.speakers)
            except DuplicateRecording as e:
                print(f"skipped {f.name}: {e}")
                continue
            print(f"added {f.name} → {rec_id}")
        print("next: octo process")
        return 0

    if args.cmd == "process":
        from .backup import daily
        from .privacy import RAW_AUDIO_DAYS, drop_idle_clips, drop_old_audio
        drop_idle_clips()
        try:
            from .export import sweep
            sweep()  # an export's leftovers, and labs set aside 14 days ago
        except OSError as e:
            print(f"couldn't sweep export leftovers: {e}")
        try:
            from .prune import prune
            if n := prune(conn):  # before the backup, so it's left out of it
                print(f"{n} superseded stage runs cleared")
        except Exception as e:  # never stops the pipeline; tried again next pass
            print(f"pruning failed: {e}")
        try:
            from .prepare import sweep as plans_past
            plans_past(conn)  # B36: plans a day past their conversation, before the backup
        except Exception as e:  # never stops the pipeline
            print(f"sweeping plans failed: {e}")
        try:
            if made := daily(conn):
                print(f"database backed up to {made}")
        except Exception as e:  # a failed backup never stops the pipeline; it's tried again next hour
            print(f"database backup failed: {e}")
        try:
            from .delivery import measure_voice
            measure_voice(conn)  # B27: before the audio goes, so the numbers outlive it
        except Exception as e:  # never stops the pipeline; tried again next pass
            print(f"voice numbers failed: {e}")
        for rec_id in drop_old_audio(conn):
            print(f"raw audio of {rec_id} deleted ({RAW_AUDIO_DAYS} days); its transcripts and insights stay")
        t0 = time.time()
        counts = run_pending(conn)
        print(f"{counts['done']} stage runs done, {counts['failed']} failed, {time.time() - t0:.0f} s")
        return 1 if counts["failed"] else 0

    if args.cmd == "sessions":
        for s in rows(conn, "SELECT s.*, r.source, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                            "WHERE s.status='active' ORDER BY r.started_at, s.start_s"):
            done = [st for st in ("diarize", "transcribe") if (r := current(conn, s["id"], st)) and r["status"] == "done"]
            state = {"diarize": "speakers", "transcribe": "transcribed"}.get(done[0], "pending") if done else "pending"
            when = datetime.fromtimestamp(s["started_at"] + s["start_s"]).strftime("%d %b %H:%M") if s["started_at"] else "?"
            print(f"{s['id']}  {when}  {clock(s['end_s'] - s['start_s']):>7}  {s['speech_s']:5.0f} s speech  {s['source']:6}  {state}")
        return 0

    if args.cmd == "show":
        text = note(conn, args.session)
        print(text)
        if args.write:
            NOTES.mkdir(exist_ok=True)
            path = NOTES / f"{args.session}.md"
            path.write_text(text)
            print(f"saved {path}")
        return 0
    return 0


def compare(conn, args) -> int:
    """Both models on the same conversation, shown as A and B in random order. The owner reads both and
    says which is more useful before the key is revealed, so the bigger model's name can't sway them."""
    import json
    import secrets

    from . import DATA
    from . import insight as I
    from .planning import settled_speakers

    key_path = DATA / "compare" / f"{args.session}.json"
    if args.reveal:
        if not key_path.exists():
            raise SystemExit(f"no comparison for {args.session} yet")
        key = json.loads(key_path.read_text())
        print(f"A = {key['A']}\nB = {key['B']}")
        return 0
    speakers = settled_speakers(conn, args.session)
    if speakers is None:
        raise SystemExit("speakers aren't settled for that conversation yet: octo confirm / octo label")
    dz = current(conn, args.session, "diarize")
    models = args.models.split(",")
    if len(models) != 2:
        raise SystemExit("--models takes exactly two, e.g. qwen3:8b,gemma3:12b")
    if secrets.randbelow(2):
        models.reverse()
    names = {s["speaker"]: s["name"] for s in speakers}
    me = {s["speaker"] for s in speakers if s["self"]}
    from .owner import load
    params = {"min_jay_speech_s": 20.0, "max_ctx": 32768, "owner": load(conn)}  # who a Claude review is about
    doc = [f"# Blind comparison: {args.session}", "",
           "Read both. For each, note which gives you something true and useful, and which is wrong or vague.",
           f"Then: `octo compare {args.session} --reveal`.", ""]
    for label, model in zip("AB", models):
        print(f"model {label}…", flush=True)
        out = I.run(load_json(dz["output"]), names, me, {**params, "model": model},
                    on_progress=lambda m: print(f"  {m}", flush=True))
        doc += ["", f"## Model {label}", f"_{clock(out['seconds'])}_"] + render_insights(out)
    NOTES.mkdir(exist_ok=True)
    path = NOTES / f"compare_{args.session}.md"
    path.write_text("\n".join(doc) + "\n")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps({"A": models[0], "B": models[1], "at": time.time()}))
    print(f"wrote {path}")
    return 0


def people_commands(conn, args) -> int:
    from . import voice as V

    if args.cmd in ("enroll", "whois"):
        tmp = A.scratch_wav()
        A.to_wav(args.file, tmp)
        speech = V.speech_only(A.read(tmp))
        tmp.unlink()

    if args.cmd == "whois":
        if len(speech) < 3 * 16000:
            raise SystemExit(f"only {len(speech) / 16000:.1f} s of speech found")
        prints = V.all_voiceprints(conn)
        if not prints:
            raise SystemExit("no voiceprints yet: octo enroll")
        emb = V.Embedder().embed(speech)
        m = V.match(emb, prints)
        for r in V.similarities(emb, prints):
            print(f"{r['name']:20} {r['similarity']:.2f}{'  (you)' if r['is_self'] else ''}")
        verdict = ("no one enrolled is close" if not m else f"close call: {m['name']} or {', '.join(m['alternatives'])}"
                   if m["ambiguous"] else f"{m['name']}")
        print(f"→ {verdict}  (named at ≥ {V.MATCH_MIN} and {V.MATCH_MARGIN} ahead; {len(speech) / 16000:.0f} s of speech)")
        return 0

    if args.cmd == "enroll":
        if len(speech) < 5 * 16000:
            raise SystemExit(f"only {len(speech) / 16000:.1f} s of speech found; record at least ~15 s of just this person")
        person = V.ensure_person(conn, args.name, is_self=args.self)
        if not V.may_keep_print(conn, person["id"]):  # (4.6) record their agreement first (People page)
            raise SystemExit(f"{person['name']}'s agreement to be recognised by voice isn't recorded: record it on the People page first")
        V.add_voiceprint(conn, person["id"], V.Embedder().embed(speech), round(len(speech) / 16000, 1), f"enrol:{args.file.name}")
        print(f"enrolled {person['name']}{' (you)' if person['is_self'] else ''} from {len(speech) / 16000:.0f} s of speech")
        print("next: octo process  (re-checks every conversation against the new voiceprint)")
        return 0

    if args.cmd == "label":
        dz = current(conn, args.session, "diarize")
        if not dz or dz["status"] != "done":
            raise SystemExit("speakers aren't separated for that session yet: octo process")
        out = load_json(dz["output"])
        spk = args.speaker.upper().removeprefix("SPEAKER ").strip()
        turns = [(t["start"], t["end"]) for t in out["turns"] if t["speaker"] == spk]
        if not turns:
            raise SystemExit(f"no speaker {spk} in {args.session} (speakers: {', '.join(out['speakers'])})")
        sess = row(conn, "SELECT s.*, r.path, r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                         "WHERE s.id=?", args.session)
        person = V.ensure_person(conn, args.name)
        consent = V.may_keep_print(conn, person["id"])  # (4.6) a new lab: only with their agreement recorded
        # After 60 days the audio is gone (D5): the name is still saved, just without a voiceprint (M32).
        got = None if sess["audio_deleted_at"] or not consent else \
            V.Embedder().embed_turns(A.read(HOME / sess["path"], sess["start_s"], sess["end_s"], channel(out, spk)), turns)
        if args.print_only:
            # The page saved the answer before this started (a refresh in the seconds this takes used
            # to ask again). Kept only while that answer stands: a rename or "Not them" since wins.
            with tx(conn):
                if not row(conn, "SELECT 1 FROM session_speakers WHERE session_id=? AND speaker=? AND person_id=? "
                                 "AND state='confirmed' AND diarize_run=?", args.session, spk, person["id"], dz["id"]):
                    print(f"{args.session}: speaker {spk} isn't {person['name']} any more; no voiceprint kept")
                    return 0
                V.drop_label(conn, args.session, spk)
                if got and got[1] >= V.MIN_PRINT_S:
                    V.add_voiceprint(conn, person["id"], got[0], got[1], f"label:{args.session}:{spk}")
                    V.prune(conn, person["id"])
            print(f"{args.session}: speaker {spk} is {person['name']}" + ("" if got and got[1] >= V.MIN_PRINT_S else " (no voiceprint kept)"))
            return 0
        V.drop_label(conn, args.session, spk)  # relabelling replaces, never adds a second voice
        if got and got[1] >= V.MIN_PRINT_S and consent:  # D1: a labelled speaker gets a stored voiceprint, for auto-tagging next time
            V.add_voiceprint(conn, person["id"], got[0], got[1], f"label:{args.session}:{spk}")
            V.prune(conn, person["id"])
        else:
            got = None
        conn.execute("INSERT OR REPLACE INTO session_speakers (session_id, speaker, person_id, state, similarity, diarize_run) "
                     "VALUES (?, ?, ?, 'confirmed', NULL, ?)", (args.session, spk, person["id"], dz["id"]))
        why = "" if got else (" (no voiceprint kept: their agreement isn't recorded)" if not consent else " (too little speech for a voiceprint)")
        print(f"{args.session}: speaker {spk} is {person['name']}{why}")
        return 0

    if args.cmd == "confirm":
        # Only clear suggestions; "Rishi or Anil?" needs a label, not a blanket confirm.
        n = conn.execute("UPDATE session_speakers SET state='confirmed' WHERE session_id=? AND state='suggested'",
                         (args.session,)).rowcount
        print(f"confirmed {n} suggested name(s)")
        return 0

    if args.cmd == "people":
        for r in rows(conn, "SELECT p.name, p.is_self, count(v.id) n, coalesce(sum(v.seconds), 0) secs FROM people p "
                            "LEFT JOIN voiceprints v ON v.person_id = p.id GROUP BY p.id ORDER BY p.is_self DESC, p.name"):
            print(f"{r['name']:20} {'(you) ' if r['is_self'] else '      '}{r['n']} voiceprint(s), {r['secs']:.0f} s of speech")
        return 0

    if args.cmd == "forget":
        print(f"forgot {args.name}" if V.forget(conn, args.name) else f"no one called {args.name}")
        return 0

    if args.cmd == "keep":
        n = conn.execute("UPDATE sessions SET status='active', kept=1, note='restored by you' WHERE id=?",
                         (args.session,)).rowcount
        print("restored; it won't be discarded again" if n else "no such session")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
