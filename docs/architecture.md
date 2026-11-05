# How octo/ is layered

This doc is from the 5 Nov tech review, risk 8. That review found 40 of 97 modules in one import cycle, held apart by
imports inside functions. In that state, an import error shows up only when its code path runs.

`tests/test_layers.py` holds the layers and checks them. Each module may import its own layer and the layers below it.

| Layer | What's in it | For example |
|---|---|---|
| 0 base | The database, files, audio and the model clients. None of the lab's own logic. | `db`, `frame`, `files`, `audio`, `asr`, `claude_code`, `providers`, `marks`, `routes.common` |
| 1 stages | The stage functions, which are pure and run in a worker, and what they're built from. | `stages`, `insight`, `review_any`, `rubric`, `corrections` |
| 2 read models | What the pages read. | `runs` (each run's state and every hold), `sessions`, `fillers`, `person`, `parts`, `review`, `split`, `eta`, `settings` |
| 3 services | What does work and writes. | `pipeline` and its planner `planning`, `coach`, `ask`, `talk`, `prepare`, `drills`, `commitments`, `privacy` (delete and Forget), `voice`, `device`, `notify` |
| 4 entry | The ways in. | `routes.*`, `cli`, `api`, `phone_api` |

## What the test checks

- Every module has a layer.
- No import goes up a layer, whether at the top of a file or inside a function.
- Every module imports cleanly at load time.

The test starts from `ALLOWED`, which lists the upward imports that existed when the check came in. `ALLOWED` may only
shrink: when one of its imports is gone, the test asks for its line to be deleted.

## First step (5 Nov): the sessions, pipeline and planning cycle

`sessions` is the read model for the list and the conversation page. It used to ask the planner whether a step was held
and why, and the planner's modules reached `sessions` again through `parts`.

**What moved:**

- The run checks went to `octo/runs.py`, below both. These are `current`, `run_reader`, the waits after a failure,
  Claude's limit and the day's calls, the signed-out and refused-key holds, the memory hold, pinned reviews and settled
  speakers.
- `pipeline` and `planning` still export these under their old names.
- The planner's two commitments checks went into `planning`.
- `device.wall_clock` moved to `marks`, and `split.apply` moved to `stages` (as `apply_splits`).
- The page's `review_outdated` flag is now added by the route, because only the planner can say it.

**Result:** `sessions`, `parts`, `split`, `search`, `measures`, `eta`, `ask`, `coach`, `notify`, `drills` and `rehearse`
no longer import `pipeline`, and none of the read models imports `planning` directly any more, except `eta`, `intention`
and `review`. Upward imports under these layers went from 29 on main to 18.

The cycle isn't gone, though:

- `sessions` still reaches `pipeline` through `events` → `notify` → `device`. `device` needs `add_recording` for the
  recorder's sync.
- `sessions` still reaches `planning` through `eta`'s forecast.
- The largest import cycle is still 40 modules.

The next cuts, each a small PR that shrinks `ALLOWED`:

- `device.sync` out of `device`, so `notify` can read the recorder's status without reaching the pipeline.
- The page's commitments, plan and privacy blocks moved from `sessions` to the routes.
- The registry in `stages` naming the planner's inputs: move it up, keep the functions down.
