// UI run state is a pure fold over the server's events (see bench/events.py).
// The server replays events on reconnect, so applying one twice must be harmless:
// events at or below lastSeq for the same run are ignored.

import type { RunEvent, RunView, StepView } from '../types'

const MAX_LOG = 500

function step(run: RunView, id: string | null): StepView | undefined {
  return run.steps.find((s) => s.id === id)
}

export function reduce(run: RunView | null, e: RunEvent): RunView | null {
  if (e.type === 'run.started') {
    return {
      runId: e.run_id,
      lastSeq: e.seq,
      state: 'running',
      startedAt: e.t,
      steps: (e.data.steps as { id: string; title: string; needs_user: boolean }[]).map((s) => ({
        id: s.id, title: s.title, needsUser: s.needs_user, state: 'queued', metrics: [], logs: [],
      })),
    }
  }
  if (!run || e.run_id !== run.runId) return run // an event from a run we never saw start
  if (e.seq <= run.lastSeq && e.type !== 'step.level') return run
  if (e.type !== 'step.level') run.lastSeq = e.seq

  const s = step(run, e.step)
  const d = e.data
  switch (e.type) {
    case 'run.state':
      run.state = d.state
      break
    case 'run.finished':
      run.state = 'idle'
      run.status = d.status
      run.reason = d.reason
      run.finishedAt = e.t
      run.prompt = null
      run.recordingAt = null
      run.level = null
      for (const x of run.steps) if (x.state === 'running' || x.state === 'queued') x.state = d.status === 'aborted' ? 'aborted' : x.state
      break
    case 'step.started':
      if (s) s.state = 'running'
      break
    case 'step.log':
      if (s) {
        s.logs.push({ line: d.line, stream: d.stream })
        if (s.logs.length > MAX_LOG) s.logs.splice(0, s.logs.length - MAX_LOG)
      }
      break
    case 'step.progress':
      if (s) s.progress = { phase: d.phase, pct: d.pct, detail: d.detail }
      break
    case 'step.metric':
      if (s) {
        const m = { key: d.key, label: d.label, value: d.value, unit: d.unit, verdict: d.verdict, previous: d.previous }
        const i = s.metrics.findIndex((x) => x.key === d.key)
        if (i >= 0) s.metrics[i] = m
        else s.metrics.push(m)
      }
      break
    case 'step.level':
      run.level = d.dbfs
      break
    case 'step.prompt':
      run.prompt = { step: e.step ?? '', phase: d.phase, message: d.message, at: e.t }
      if (d.phase === 'silent') run.recordingAt = e.t
      break
    case 'step.artifact':
      if (!s) break
      if (d.kind === 'recording') s.recording = e.run_id
      if (d.kind === 'levels') {
        s.levels = {
          levels: d.levels, window_s: d.window_s, quiet_window: d.quiet_window,
          speech_window: d.speech_window, cue_at: d.cue_at, seconds: d.seconds,
        }
      }
      if (d.kind === 'transcript') s.transcripts = d.results
      break
    case 'step.finished':
      if (s) {
        s.state = d.status
        s.summary = d.summary
        s.hint = d.hint
        s.duration = d.duration_s
        s.progress = null
      }
      if (run.prompt?.step === e.step) {
        run.prompt = null
        run.recordingAt = null
      }
      break
  }
  return run
}

export function replay(events: RunEvent[]): RunView | null {
  return events.reduce<RunView | null>((run, e) => reduce(run, e), null)
}
