// Practice drills (B18): where a take can be recorded, in what format, and the words for a take's rows.
import { measureValue } from './format'
import type { DrillRow } from './types'

// The lab reads what macOS's afconvert reads (octo/audio.py): MP4 with AAC, which Safari and iOS record. Chrome's
// WebM/Opus isn't among them.
export const TYPES = ['audio/mp4;codecs=mp4a.40.2', 'audio/mp4']

/** The first type this browser can record that the lab can read, or null. */
export function recordType(supported: (t: string) => boolean): string | null {
  return TYPES.find((t) => supported(t)) ?? null
}

/** Why a take can't be recorded here, or null when it can. */
export function cantRecord(env: { inMacApp: boolean; mic: boolean; type: string | null }): string | null {
  // The Octo app's window (macapp/Octo.swift) doesn't ask macOS for the microphone: on the Mac it plays takes back only.
  if (env.inMacApp) return 'The Octo app on the Mac can play takes back but not record them. Record on your phone (the Lab tab), or open the lab in Safari.'
  if (!env.mic) return 'This browser can\'t use the microphone here.'
  if (!env.type) return 'This browser records only in a format the lab can\'t read. Record on your phone, or in Safari.'
  return null
}

/** "last time 4.1": the take before this one, when there is one. */
export function lastText(r: DrillRow): string {
  return r.last == null ? '' : `last time ${measureValue(r.measure, r.last)}`
}

/** "About usual is 4.0–5.0: the middle half of your 7 takes before this." */
export function takesSpread(r: DrillRow, minWords: number, words: number | null): string {
  if (r.state === 'short') return `Under ${minWords} words in this take (${words ?? 0}): too few to compare.`
  if (r.state === 'no_usual') return `Your usual needs 5 takes before this one (the pitch memos count); there ${r.conversations === 1 ? 'is' : 'are'} ${r.conversations}.`
  if (r.state === 'unmeasured') return 'This take has no word times, so this can\'t be counted.'
  if (r.lo == null || r.hi == null) return ''
  return `About usual is ${measureValue(r.measure, r.lo)}–${measureValue(r.measure, r.hi)}: the middle half of your ${r.conversations} takes before this.`
}
