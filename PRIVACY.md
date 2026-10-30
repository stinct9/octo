# Privacy

Octo is built so your conversations stay yours. There are no accounts, no servers of ours and no telemetry: nothing
reaches Octo's makers, ever. This page lists everything that can leave your Mac, and when.

## What never leaves your Mac

- **Audio.** Recordings, the recorder's chunks, practice takes. Transcription and telling voices apart run on your Mac.
- **Voiceprints.** Kept only for speakers you name, only on your Mac. Forget someone on People and their prints, their
  name and their lines in the recordings go. In a new lab, someone's voice is recognised only once you've recorded
  that they agreed.
- **Everything up to the transcript**: speech detection, speakers, who said what, the numbers.

## What leaves it, only when you ask, and only to the model you chose

You choose the model for reviews in Settings → Models and keys: Claude Code on this Mac (your own Claude plan), a
service on your own API key, or a model running on your Mac (then nothing below leaves at all).

| When | What is sent |
|---|---|
| A conversation is reviewed | its transcript text, the names in it, its numbers, and your principles |
| Hindi or mixed speech (once you're confirmed in it) | the two local drafts' text, to be merged into one |
| Commitments | the conversation's text, and the open commitments with the same people |
| The coach, when you ask | the reviews shown now, your marks and notes on them, your focus, numbers and principles; never what you marked not right |
| Ask your conversations | your question and the lines picked for it on your Mac (at most 400 lines from several conversations); the page says which |
| Prepare for a conversation | the lines of conversations with that person, and lines naming them elsewhere, after you've seen and ticked them |
| Talk it through | one conversation's review material, the review, your reflection and marks, and the thread so far |
| Rehearse | what the reviews read in the person, your description, and the typed exchange |
| Practice | a take's words and its prompt |

Deleting a conversation, or forgetting someone in it, deletes every answer, note or plan that read it.

## Other connections, all your choice

- **TickTick:** a step's title, details, due date and a link back, only when you press *Add to TickTick*.
- **A calendar link (ICS):** fetched from your calendar's host every 5 minutes; only the request goes out. Calendars on
  this Mac are read by the Octo app and never leave it.
- **Your phone:** the lab's pages reach your paired iPhone on your home Wi-Fi, or through your own Tailscale network
  (end-to-end encrypted). Deleting and forgetting work from home Wi-Fi only.
- **Downloads:** the speech models from Hugging Face (with your token), and, on a release install, a check for a newer
  version (one small file) each hour. Nothing about you is sent with either.

Claude Code's own telemetry and error reporting are switched off for Octo's calls.

## On your Mac

The lab listens on 127.0.0.1 only and answers only the Octo app or a browser let in with `octo open`. Its folders are
readable by you alone; keys and tokens sit in files only you can read. The database is backed up daily on this Mac,
none kept past 14 days: Delete and Forget reach the backups within 14 days. An export is an encrypted disk image with your password.

Recording people may need their consent where you live. That's yours to know and follow.
