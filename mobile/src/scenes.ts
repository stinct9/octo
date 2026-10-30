// What the app's panel shows (docs/phone/design-review.md §6–§7): "scenes" are the octopus alone, centred, built
// from the recorder's own pieces; "mirrors" are the recorder's exact screens, captioned "Your recorder shows".
// Each draws a frame for `ms` since the scene began; `still` is the frame shown with reduced motion, `anim`
// marks a short animation that runs at the recorder's fast cadence (50 ms a frame instead of 150).
import { OD } from './octo'
import type { Frame } from './octo'

export interface Scene {
  key: string
  draw(f: Frame, ms: number): void
  still?: number
  anim?: number
  /** For VoiceOver: mirrors only (a scene is decoration; the headline says it). */
  label?: string
}

const CX = 64, CY = 30
const { clear, octopus, octopusEyes, arc, text, textWidth, disc, clamp01, easeOut, easeBack, AWAKE, ASLEEP, ALERT } = OD
const idiv = (a: number, b: number) => Math.trunc(a / b)

export const asleep: Scene = {
  key: 'asleep', still: 0,
  draw(f, ms) {
    clear(f)
    octopus(f, CX, CY, 14, ASLEEP, ms * 0.0015)
    text(f, CX + 16, Math.floor(ms / 700) % 2 ? 6 : 8, 'z', 1)
  },
}

export const search: Scene = {
  key: 'search', still: 400,
  draw(f, ms) {
    clear(f)
    const t = ms % 1600
    if (t < 900) arc(f, CX, CY, 16 + Math.round(52 * easeOut(t / 900)), 0, 358)
    octopus(f, CX, CY, 14, ASLEEP, ms * 0.0015)
  },
}

/** It wakes: grows with a pop and opens its eyes. */
export const found: Scene = {
  key: 'found', still: 600, anim: 600,
  draw(f, ms) {
    clear(f)
    const r = 8 + Math.round(6 * easeBack(ms / 450))
    octopusEyes(f, CX, CY, r, clamp01((ms - 150) / 250), 0, ms * 0.004)
  },
}

/** Awake, with a slow ripple: connecting. */
export const waking: Scene = {
  key: 'waking', still: 1000,
  draw(f, ms) {
    clear(f)
    const t = ms % 1600
    if (t < 1100) arc(f, CX, CY, 18 + Math.round(40 * easeOut(t / 1100)), 0, 358)
    octopus(f, CX, CY, 14, AWAKE, ms * 0.004)
  },
}

export const listening: Scene = {
  key: 'listening', still: 900,
  draw(f, ms) {
    clear(f)
    octopus(f, CX, CY, 14, AWAKE, ms * 0.004)
    const waves = 1 + (Math.floor(ms / 400) % 3)
    for (let k = 0; k < waves; k++) arc(f, CX, CY, 18 + 4 * k, 225, 315)
  },
}

export const alert: Scene = {
  key: 'alert', still: 0,
  draw(f, ms) {
    clear(f)
    octopus(f, CX, CY, 14, ALERT, ms * 0.01)
    text(f, CX + 20, 4, '!', 2)
  },
}

/** The recorder's start animation, octopus only (od_anim_start): a ring, a pop, eyes open, waves one by one. */
export const start: Scene = {
  key: 'start', still: 1200, anim: 1200,
  draw(f, t) {
    clear(f)
    if (t < 650) arc(f, CX, CY, 4 + Math.trunc(80 * easeOut(t / 650)), 0, 358)
    const r = 8 + Math.round(6 * easeBack(t / 450))
    octopusEyes(f, CX, CY, r, clamp01((t - 250) / 250), 0, t * 0.004)
    const waves = t < 500 ? 0 : Math.min(3, 1 + Math.trunc((t - 500) / 150))
    for (let k = 0; k < waves; k++) arc(f, CX, CY, 18 + 4 * k, 225, 315)
  },
}

/** Connected while it isn't recording: the same pop, a look, then the eyes close again (od_anim_stop's eyes). */
export const hello: Scene = {
  key: 'hello', still: 2000, anim: 1700,
  draw(f, t) {
    clear(f)
    if (t < 650) arc(f, CX, CY, 4 + Math.trunc(80 * easeOut(t / 650)), 0, 358)
    const r = 8 + Math.round(6 * easeBack(t / 450))
    const open = clamp01((t - 250) / 250) * (1 - clamp01((t - 1000) / 350))
    octopusEyes(f, CX, CY, r, open, 0, t * 0.003)
    if (t > 1500) text(f, CX + 16, 8 - Math.min(2, Math.trunc((t - 1500) / 60)), 'z', 1)
  },
}

/** The recorder's stop animation, octopus only (od_anim_stop). */
export const stop: Scene = {
  key: 'stop', still: 1200, anim: 1200,
  draw(f, t) {
    clear(f)
    const move = easeOut(t / 900)
    const cx = CX - Math.round(2 * move), cy = CY - Math.round(4 * move) + 4
    octopusEyes(f, cx, cy, 14, 1 - clamp01((t - 300) / 350), 0, t * 0.003)
    const waves = t < 300 ? 3 - Math.trunc(t / 100) : 0
    for (let k = 0; k < waves; k++) arc(f, cx, cy, 18 + 4 * k, 225, 315)
    if (t > 1000) text(f, cx + 16, 8 - Math.trunc((t - 1000) / 60), 'z', 1)
  },
}

// ---- mirrors: the recorder's own screens ----

/** Its code screen with blanks: the app never knows the digits. */
export const code: Scene = {
  key: 'code', still: 0, label: 'Your recorder\'s screen: Phone code, six digits',
  draw(f, ms) {
    clear(f)
    text(f, idiv(128 - textWidth('Phone code', 1), 2), 4, 'Phone code', 1)
    const x0 = idiv(128 - textWidth('000000', 3), 2)
    for (let i = 0; i < 6; i++) OD.fill(f, x0 + i * 18 + 1, 40, 13, 3, 1)
    text(f, idiv(128 - textWidth('type it on the phone', 1), 2), 52, 'type it on the phone', 1)
    if (ms % 1000 < 500) disc(f, 122, 7, 2, 1)
  },
}

export const setup: Scene = { key: 'setup', still: 0, label: 'Your recorder\'s screen: Set me up, USB to your Mac', draw: (f, ms) => OD.setup(f, ms) }

export const updating = (pct: number): Scene => ({
  key: 'updating', still: 0, label: pct >= 100 ? 'Your recorder\'s screen: Restarting' : `Your recorder's screen: Updating, ${pct}%`,
  draw: (f, ms) => OD.updating(f, Math.min(pct, 100), 100, ms),
})

export const result = (kind: 'checking' | 'done' | 'failed' | 'undone', fw: string): Scene => {
  const k = { checking: OD.CHECKING, done: OD.DONE, failed: OD.FAILED, undone: OD.UNDONE }[kind]
  const words = { checking: 'Checking with the Mac', done: 'Updated, all good', failed: 'Update failed', undone: 'Rolled back' }[kind]
  return { key: `result-${kind}`, still: 600, anim: kind === 'done' ? 600 : undefined, label: `Your recorder's screen: ${words}, ${fw}`, draw: (f, ms) => OD.updateResult(f, k, fw, ms) }
}
