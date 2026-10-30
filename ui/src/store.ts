// The single reactive store. Server events flow in through `apply`; components read `state`
// and call the actions. No Pinia: one store, one page.

import { computed, reactive } from 'vue'
import { api, subscribe } from './api'
import { reduce, replay } from './lib/reduce'
import type { RunEvent, RunSummary, RunView, StepInfo, StepView } from './types'

export const state = reactive({
  catalog: [] as StepInfo[],
  run: null as RunView | null,
  history: [] as RunSummary[],
  port: null as string | null,
  connected: false,
  selected: null as string | null,
  follow: true, // selection follows the running step until the user picks one
  error: null as string | null,
  // Each step's most recent real result from any run, so the idle screen shows the last known
  // state of every component even after a run of just one step.
  lastKnown: {} as Record<string, StepView & { at: number }>,
})

export const busy = computed(() => !!state.run && state.run.state !== 'idle')

const MEASURED = new Set(['pass', 'warn', 'fail'])

function remember(run: RunView): void {
  for (const s of run.steps) {
    if (MEASURED.has(s.state)) state.lastKnown[s.id] = { ...s, at: run.startedAt }
  }
}

function apply(e: RunEvent): void {
  state.run = reduce(state.run, e)
  if (e.type === 'run.started') {
    state.follow = true
    state.error = null
  }
  if (e.type === 'step.started' && state.follow) state.selected = e.step
  if (e.type === 'run.finished') {
    if (state.run) remember(state.run)
    void refreshHistory()
  }
}

async function refreshHistory(): Promise<void> {
  try {
    state.history = await api.runs(8)
  } catch { /* shown via connection state */ }
}

export async function init(): Promise<() => void> {
  const [catalog] = await Promise.all([api.steps(), refreshHistory()])
  state.catalog = catalog
  // Last known result per step: the newest run in which each step was actually measured.
  const runs = await api.runs(30)
  const wanted = new Map<string, string>() // step -> run_id
  for (const c of catalog) {
    const r = runs.find((x) => MEASURED.has(x.steps[c.id]))
    if (r) wanted.set(c.id, r.run_id)
  }
  const replayed = new Map<string, RunView | null>()
  for (const runId of new Set(wanted.values())) replayed.set(runId, replay(await api.runEvents(runId)))
  for (const [stepId, runId] of wanted) {
    const run = replayed.get(runId)
    const s = run?.steps.find((x) => x.id === stepId)
    if (run && s) state.lastKnown[stepId] = { ...s, at: run.startedAt }
  }
  // The header's "last run" is the newest run that measured anything.
  const last = runs.find((r) => r.status !== 'running' && Object.keys(r.steps).length > 0)
  if (last) state.run = replayed.get(last.run_id) ?? replay(await api.runEvents(last.run_id))
  state.selected = catalog[0]?.id ?? null
  return subscribe({
    onEvent: apply,
    onDevice: (port) => { state.port = port },
    onConnection: (open) => { state.connected = open },
  })
}

async function act(fn: () => Promise<unknown>): Promise<void> {
  try {
    state.error = null
    await fn()
  } catch (err) {
    state.error = err instanceof Error ? err.message : String(err)
  }
}

export const actions = {
  runAll: () => act(() => api.start(['all'])),
  runOne: (id: string) => act(() => api.start([id])),
  ready: () => act(() => api.ready()),
  cancel: () => act(() => api.cancel()),
  select(id: string) {
    state.selected = id
    state.follow = false
  },
}
