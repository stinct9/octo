// How the owner showed up (B10, octo/delivery.py): the words on each row of the glance. Neutral, one ink:
// fewer / about usual / more, never better or worse (coaching plan A4).
import { measureValue } from './format'
import type { DeliveryMeasure, DeliveryRow } from './types'

export const DELIVERY_NAME: Record<DeliveryMeasure, string> = {
  fillers: 'Fillers', pauses: 'Hesitation pauses', restarts: 'Restarts', hedges: 'Hedges', pace: 'Pace',
  longest: 'Longest stretch', patience: 'Patience', turns: 'Turns', their_longest: 'Their longest stretch',
  pitch_range: 'Pitch range', loudness: 'Loudness', pushed_pitch: 'Pitch, pushed back', pushed_loudness: 'Loudness, pushed back',
}
const UNIT: Record<DeliveryMeasure, string> = {
  fillers: 'per 100 words', pauses: 'per 100 words', restarts: 'per 100 words', hedges: 'per 100 words',
  pace: 'words a minute', longest: 'without a pause', patience: 'before you speak', turns: 'changes a minute',
  their_longest: 'without being cut off', pitch_range: 'semitones', loudness: 'dB against the others',
  pushed_pitch: 'semitones against the rest', pushed_loudness: 'dB against the rest',
}
// Low and high, said the way each measure is: a slower pace isn't "fewer".
const SIDES: Record<DeliveryMeasure, [string, string]> = {
  fillers: ['fewer', 'more'], pauses: ['fewer', 'more'], restarts: ['fewer', 'more'], hedges: ['fewer', 'more'],
  pace: ['slower', 'faster'], longest: ['shorter', 'longer'], patience: ['shorter', 'longer'], turns: ['fewer', 'more'],
  their_longest: ['shorter', 'longer'], pitch_range: ['narrower', 'wider'], loudness: ['quieter', 'louder'],
  pushed_pitch: ['narrower', 'wider'], pushed_loudness: ['quieter', 'louder'],
}

/** "3.1 per 100 words", "152 words a minute", "42 s without a pause"; "–" without a number. */
export function deliveryValue(m: DeliveryMeasure, v: number | null): string {
  return v == null ? '–' : `${measureValue(m, v)} ${UNIT[m]}`
}

/** "usually 4.6", or empty when there's no usual to give. */
export function usualText(r: DeliveryRow): string {
  return r.usual == null ? '' : `usually ${measureValue(r.measure, r.usual)}`
}

/** The state word: where this conversation sits in his own spread, or why it can't say. */
export function stateWord(r: DeliveryRow): string {
  switch (r.state) {
    case 'low': return `${SIDES[r.measure][0]} than usual`
    case 'high': return `${SIDES[r.measure][1]} than usual`
    case 'about': return 'about usual'
    case 'short': return 'too short to tell'
    case 'no_usual': return 'no usual yet'
    case 'off': return 'not on your line'
    case 'unmeasured': return 'not measured here'
    case 'no_audio': return 'audio deleted'
  }
}

/** "About usual is 4.0–5.0, the middle half of your 12 conversations before this." */
export function spreadText(r: DeliveryRow): string {
  if (r.lo == null || r.hi == null) return ''
  const v = (x: number) => measureValue(r.measure, x)
  return `About usual is ${v(r.lo)}–${v(r.hi)}: the middle half of your ${r.conversations} conversations before this.`
}

/** What a focus starts out saying for a measure, with no direction in it; the owner words it their way. */
export function focusText(m: DeliveryMeasure): string {
  if (m === 'their_longest') return 'Let them finish'
  if (m === 'pushed_pitch' || m === 'pushed_loudness') return `Watch my ${m === 'pushed_pitch' ? 'pitch' : 'loudness'} when pushed back`
  return `Watch my ${m === 'turns' ? 'turn-taking' : DELIVERY_NAME[m].toLowerCase()}`
}
