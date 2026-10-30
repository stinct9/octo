# SPIKE: calls on the Mac (B19, 20 Oct)

A throwaway test tool, not part of the Octo app: `octo launcher install` doesn't build it and `octo deploy`
doesn't look here. It answered B19's spike questions; the results are in `docs/learnings.md` ("Calls on the
Mac") and the recommended build in `docs/backlog.md` (B19).

    ./build.sh /some/scratch/folder          # CallTap.app, ad hoc signed
    open -W --stdout LOG --stderr LOG CallTap.app --args record PID SECONDS OUT.wav [tap-only]
    CallTap.app/Contents/MacOS/CallTap procs | watch SECONDS     # no permission needed
    python -I analyse.py OUT.wav [WINDOW_S] [EXPECTED_S]
    python -I analyse.py --cancel OUT.wav FIT_FROM FIT_TO
    python -I aec.py OUT.wav AEC.wav FAR_FROM FAR_TO NEAR_FROM NEAR_TO   # needs livekit, in a scratch venv

Record only test audio, into a scratch folder, and delete it after: never a real call, never into the lab.
