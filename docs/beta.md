# Octo's beta

Thank you for trying Octo before it's sold. It's a lab on your own Mac that reviews your conversations against
principles you write yourself, and tells you something about how you showed up in them. The beta is to find
out whether that works for people other than the one who built it.

## What we'd like to hear

Three things, over four weeks:

1. **Could you install it on your own?** Tell us where you got stuck, if anywhere.
2. **Did the reviews tell you anything new** about how you showed up?
3. **Are you still using it in week four?**

The recorder isn't part of the beta: you'll use voice memos from your phone.

## What you'll do

1. Install it: open Terminal (⌘ Space, type Terminal, Return), paste the line you were sent and press Return. An
   Apple Silicon Mac with 16 GB and 20 GB free (about 35 GB with the iPhone app); an hour or more on most lines,
   nearly all of it downloads that carry on while you do the rest. If you get stuck, note where.
2. On **Set up Octo** (it opens when the install ends), one step at a time, each checked before the next: sign in to
   Hugging Face for the speech models, say who you are, write your principles in your own words, choose a model for the
   reviews (your Claude plan through Claude Code, or your own API key: about $0.10 plus $0.006 a minute of
   conversation on Claude Opus 5.5, tested when you connect it), and, if you like, put the Octo app on your iPhone.
3. For four weeks, record conversations you're in with your phone's voice memos, with the others' agreement, and
   add them to the lab: with the Octo iPhone app, its Memos tab; or the memo → Share → AirDrop → your Mac, then
   Conversations → Add recordings (or drop it on the page).
4. Read each review and answer its one question: what here was new to you (or "Nothing new this time"). Mark a
   step **Did it** when you do it.
5. At the end, open Progress → **For the beta**, look at the results file, save it and send it back with a line
   on where you got stuck installing (if you did).

## What we see, and what we don't

- **We see:** the results file, and only if you send it. It has counts: conversations, reviews, how many told
  you something new, steps you acted on, items you marked not right, coach notes and your marks on them, how
  many weeks you used it, and which kind of model ran the reviews. You see it before it goes anywhere.
- **We never see:** your recordings, transcripts, reviews, principles, the people in your conversations, or
  your keys. There are no accounts and no servers of ours.
- **Your model provider sees** each conversation's text when it's reviewed (on your key, under their terms).

## Stopping

Any time: in the Octo app, **Octo → Uninstall Octo…**, or in Terminal
`~/Library/Application\ Support/octo/app/.venv/bin/octo uninstall`. It asks whether to keep your recordings and
reviews before it deletes anything.

## For the person running the beta

- Testers' results files arrive by email; nothing is collected automatically.
- No pass mark (19 Oct): the beta is for finding where people get stuck and whether it helps them, as open
  source projects usually do it, not a gate. Nothing waits on a number from it (docs/gate-plan.md).
