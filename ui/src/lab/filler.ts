// The filler cue's targets on the Fillers page (B29, octo/filler_loop.py readiness and last_training): plain words for each
// filler's state, a training's progress and its result.

export interface Training {
  started_at: number
  finished_at?: number
  running?: boolean
  step?: 'gathering' | 'testing' | 'everything' | 'small' | 'done'
  done?: number
  trainings?: number
  conversations?: number
  examples?: number
  error?: string
  cancelled?: boolean
  caught?: number | null
  before?: number | null
  candidate?: boolean
}

export interface Target {
  target: string
  phrases: string[]
  his: number
  others: number
  leans: number | null
  status: 'on' | 'ready' | 'growing' | 'ordinary' | 'none'
  need: number
  weeks_left: number | null
  training: Training | null
}

export const FLOOR = 1 / 3  // spikes/b20-filler-cue/loop.py: a first model is offered only if it catches this much

export const STEPS: { step: Training['step']; label: string }[] = [
  { step: 'gathering', label: 'Gathering your examples' },
  { step: 'testing', label: 'Testing it on conversations it hasn’t heard' },
  { step: 'everything', label: 'Training on everything' },
  { step: 'small', label: 'Making it small enough for the recorder' },
]

/** The hesitations are one target, named "uh" on the recorder. */
export function alsoLine(t: Target): string | null {
  return t.phrases.length > 1 ? 'uh, um and the like (one sound)' : null  // the hesitations, the only group
}

export function leansLine(t: Target): string | null {
  if (t.leans == null) return null
  return t.leans >= 1 ? `You say it ${t.leans}× as often as the people you talk with`
    : 'The people you talk with say it more than you'
}

/** The row's state on the right, when nothing is training it: what you can do with it. */
export function stateLine(t: Target): string {
  switch (t.status) {
    case 'on': return 'On the recorder'
    case 'ready': return `${t.his} examples · ready to train`
    case 'growing': return t.weeks_left == null ? `Not enough yet: ${t.his} examples so far`
      : `Not enough yet: ${t.his} examples so far · enough in about ${weeks(t.weeks_left)}`
    case 'ordinary': return `${t.his} examples · mostly an ordinary word for you`
    default: return 'Not enough yet: none so far'
  }
}

function weeks(w: number): string {
  if (w < 1.5) return 'a week'
  if (w < 9) return `${Math.round(w)} weeks`
  return `${Math.round(w / 4.3)} months`
}

/** Minutes left in a running training, from how long its tests took so far; null until one is done. */
export function minutesLeft(t: Training, now: number): number | null {
  if (!t.running || t.step !== 'testing' || !t.done || !t.trainings) return null
  const each = (now - t.started_at) / t.done
  return Math.max(1, Math.round((each * (t.trainings - t.done) + 180) / 60))  // ~3 min to train on all and convert
}

/** What the last finished training found, in a line; null if it's running or never ran. */
export function resultLine(target: string, t: Training | null): string | null {
  if (!t || t.running) return null
  if (t.cancelled) return 'Training was cancelled.'
  if (t.error) return `The last training failed: ${t.error}`
  if (t.caught == null) return null
  const pct = Math.round(t.caught * 100)
  if (t.candidate) return `In testing it caught ${pct}% of your “${target}”s, with at most 3 wrong flashes an hour.`
  if (t.before != null) return `Retrained: ${pct}% caught in testing against ${Math.round(t.before * 100)}% for the one on the recorder. Not better yet.`
  return `In testing it caught ${pct}% of your “${target}”s: a cue needs at least 1 in 3 to be offered. Train again as your examples grow.`
}
