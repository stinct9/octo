# Contributing

Thank you for wanting to help. Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md).

- **Getting going in a clone.** `./install.sh --check`, then `./install.sh` (it makes `.venv` and builds the pages);
  or, for the tests alone, `uv venv --python 3.12 .venv`, then `uv pip install --require-hashes --python
  .venv/bin/python -r requirements.lock` and `uv pip install --python .venv/bin/python --no-deps -e .`.
  `.venv/bin/python -m tests.demo_lab [--port 8799]` serves a made-up lab (no recordings of yours) to look at the
  pages, once they're built (`./install.sh` does it, or `npm install && npm run build` in `ui/`). How the code is layered, and what may import what: [docs/architecture.md](docs/architecture.md).
- **Tests first.** `.venv/bin/python -m pytest` for the lab, `npm test` in `ui/` and `mobile/`. The firmware's
  plain-C parts are compiled and tested on the Mac by the same pytest run.
- **Measure, then decide.** A change of model or setting comes with what it was tested on and the numbers.
- **Privacy is the product.** Nothing that leaves the Mac without the owner choosing it; no telemetry; audio
  and voiceprints never leave it at all.
- **Principles belong to their owner.** Nothing in Octo writes, suggests or completes someone's principles.
- Never commit recordings, transcripts, keys or `secrets.h` (`git config core.hooksPath .githooks` refuses them).
- **Licence.** What you contribute is under the project's licences: AGPL-3.0 for software, CERN-OHL-S-2.0 for
  anything in `hardware/`.
