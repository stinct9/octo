# Security

Please report a vulnerability privately, not in a public issue: write to **support@octolab.online** with "Security"
in the subject, or use GitHub's *Report a vulnerability* on this repository's Security tab. Say what you found, how
to reproduce it, and what it reaches. You'll hear back within a week. Please give us a reasonable time to fix it
before you publish.

What Octo protects, in short: the recorder signs and encrypts what it keeps (its chunks are encrypted to the
paired Mac's key); every request between them is signed with a shared key and a one-time nonce; its Bluetooth
values need a bonded, authenticated link; API keys live in a file readable only by the Mac's user; the lab
listens on 127.0.0.1 and answers only a browser or app let in on this Mac (and, if switched on, HTTPS on the home
network for a paired phone). What leaves the Mac, and when: [PRIVACY.md](PRIVACY.md).
