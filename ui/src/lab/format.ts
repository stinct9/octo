import type { AsideKind, Device, Measure, SpeakerChip, Todo } from './types'

/** 0:39, 6:02, 1:02:05 */
export function mss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Today, 21:40", "Yesterday, 08:05", "Tue 29 Sep, 20:30", "Fri 30 Sep 2022, 18:02" (another year) */
export function when(unix: number | null, now = new Date()): string {
  if (!unix) return 'Unknown time'
  const d = new Date(unix * 1000)
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(now) - day(d)) / 86_400_000)
  if (diff === 0) return `Today, ${time}`
  if (diff === 1) return `Yesterday, ${time}`
  const year = d.getFullYear() === now.getFullYear() ? '' : ` ${d.getFullYear()}`
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}${year}, ${time}`
}

/** Today as YYYY-MM-DD in local time (toISOString is UTC: wrong before 05:30 in Bengaluru). */
export function today(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

/** "Thu 8 Oct" for a YYYY-MM-DD due date (a calendar day, no time zone shift). */
export function dueDay(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  const x = new Date(y, m - 1, d)
  return `${DAYS[x.getDay()]} ${x.getDate()} ${MONTHS[x.getMonth()]}`
}

const STAGE: Record<string, string> = {
  transcribe: 'Transcribing', speakers: 'Separating speakers', diarize: 'Matching words to speakers', identify: 'Recognising voices',
  numbers: 'Counting', insight: 'Reviewing the conversation', commitments: 'Looking for commitments',
}

/** When something will be ready, as a person would say it (B3): "in about 4 min", "about 16:20",
 *  "about Tue 09:10". Never a time already past: late says "taking longer than usual". */
export function etaText(unix: number | null | undefined, late = false, now = Date.now()): string | null {
  if (late) return 'taking longer than usual'
  if (!unix) return null
  const min = (unix * 1000 - now) / 60_000
  if (min < 1) return 'any moment'
  if (min < 10) return `in about ${Math.ceil(min)} min`
  if (min < 60) return `in about ${Math.ceil(min / 5) * 5} min`
  const d = new Date(unix * 1000)
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
  return new Date(now).toDateString() === d.toDateString() ? `about ${time}` : `about ${DAYS[d.getDay()]} ${time}`
}

/** What a recording set aside as not a conversation was (B5). */
export const ASIDE: Record<AsideKind, string> = { tv: 'TV', music: 'Music', other: 'Something else' }

/** What's left to do, as the list shows it. `tone` picks the colour. */
export function todoLabel(t: Todo): { text: string; tone: 'muted' | 'warn' | 'accent' | 'done' | 'fail' } {
  switch (t.kind) {
    case 'processing':
      if (t.eta || t.late) {  // when, not which step (B3): the step is on the page
        const head = `${t.then === 'review' ? 'Review' : 'Ready for you'} ${etaText(t.eta, t.late)}`
        return { text: t.detail && t.queued ? `${head} · ${t.detail}` : head, tone: 'muted' }
      }
      if (t.queued) return { text: `Queued: ${(STAGE[t.stage] ?? t.stage).toLowerCase()} · ${t.detail ?? `step ${t.step} of ${t.of}`}`, tone: 'muted' }
      return { text: `${STAGE[t.stage] ?? t.stage}… · ${t.detail ?? `step ${t.step} of ${t.of}`}`, tone: 'muted' }
    case 'failed': return { text: t.hint ?? `Failed while ${(STAGE[t.stage] ?? t.stage).toLowerCase()}${t.auto_retry ? ' · will try again' : ''}`, tone: 'fail' }
    case 'confirm': return { text: t.hindi ? 'Confirm speakers · then Hindi is transcribed' : 'Confirm speakers', tone: 'warn' }
    case 'not_you': return { text: 'Without you · numbers only', tone: 'done' }
    case 'short': return { text: /^nothing/i.test(t.reason) ? 'Nothing in the review to answer' : 'Numbers only · too short for insights', tone: 'done' }
    case 'paused': return { text: `${t.reason} · not transcribed yet`, tone: 'muted' }
    case 'review': return { text: 'Ready to review', tone: 'accent' }
    case 'reviewed': return { text: 'Reviewed', tone: 'done' }
    case 'aside': return { text: `Not a conversation · ${ASIDE[t.what]}`, tone: 'muted' }
    case 'part': return { text: 'Part of one discussion · reviewed as a whole', tone: 'done' }
  }
}

/** Needs something = what the owner can act on: speakers to confirm, a review to read, a step that failed and
 *  won't try again by itself. Processing, and failures that will retry, are a quiet count instead. */
export function needsYou(t: Todo): boolean {
  return t.kind === 'confirm' || t.kind === 'review' || (t.kind === 'failed' && !t.auto_retry)
}
export function inProgress(t: Todo): boolean {
  return t.kind === 'processing' || (t.kind === 'failed' && !!t.auto_retry)
}

/** Colour slot per speaker: the owner is always the accent; others in order of appearance, 8 colours,
 *  then grey (the 1 Oct call has 10 speakers; repeating colours made two people look like one). */
export function speakerColors(speakers: Pick<SpeakerChip, 'letter' | 'self'>[]): Record<string, string> {
  const out: Record<string, string> = {}
  let i = 0
  for (const s of speakers) out[s.letter] = s.self ? 'me' : i < 8 ? `s${++i}` : 'sx'
  return out
}

/** Same bar as the notification (octo/notify.py QUIET_H). */
export const QUIET_H = 14

const DEVICE_TROUBLE: Record<string, string> = {
  no_card: 'no SD card', card_full: 'card full', card_error: 'SD card problem', mic_error: 'mic problem',
}

/** The recorder chip (5 Oct: online or not, then what it's doing): "Online · Recording",
 *  "Online · Not recording", "Offline · seen Today, 14:02", or a problem. Online = it answered the last
 *  check (every 30 s). Offline, it may well be recording; the Mac just can't see it. */
export function deviceLabel(d: Device, now = Date.now() / 1000): { text: string; tone: 'rec' | 'ok' | 'muted' | 'warn' | 'fail'; home: boolean } | null {
  if (!d.configured) return null
  if (!d.seen_at) return { text: 'Recorder not seen yet', tone: 'muted', home: false }
  // Checked every 30 s while the lab runs; 3 min without an answer and it's no longer claimed online.
  const home = !d.error && d.checked_at !== null && now - d.checked_at < 3 * 60 && d.seen_at >= d.checked_at - 1
  // A workday away is normal (it records; the Mac can't see it). Longer, while set to record: say so.
  const quiet = now - d.seen_at > QUIET_H * 3600 && d.want_on !== false
  if (!home) return { text: `Offline · seen ${when(d.seen_at, new Date(now * 1000))}`, tone: quiet ? 'warn' : 'muted', home }
  if (d.state && DEVICE_TROUBLE[d.state]) return { text: `Online · ${DEVICE_TROUBLE[d.state]}`, tone: 'fail', home }
  if (d.state === 'recording') return { text: 'Online · Recording', tone: 'rec', home }
  if (d.state === 'off') return { text: 'Online · Not recording', tone: 'ok', home }
  return { text: 'Online · Starting', tone: 'ok', home }
}

/** Roughly how long: "about 40 min", "about 2 h". */
export function roughly(seconds: number): string {
  if (seconds < 90) return 'about a minute'
  if (seconds < 90 * 60) return `about ${Math.round(seconds / 60)} min`
  return `about ${Math.round(seconds / 3600 * 2) / 2} h`.replace('.5 h', '½ h')
}

/** What a model switch redoes, in one sentence. */
export function impactText(i: { conversations: number; later: string[]; estimate_s: number | null; estimate_partial: boolean }): string {
  if (i.conversations === 0) return 'Nothing to redo yet: new conversations will use it.'
  const n = `${i.conversations} conversation${i.conversations === 1 ? '' : 's'}`
  const later = i.later.map((s) => s.toLowerCase())
  const list = later.length > 1 ? `${later.slice(0, -1).join(', ')} and ${later[later.length - 1]}` : later[0]
  const then = list ? `, then ${list}` : ''
  const time = i.estimate_s ? ` Roughly ${roughly(i.estimate_s).replace('about ', '')}${i.estimate_partial ? ' or more' : ''}, in the background.` : ''
  return `Redoes this step for ${n}${then}.${time}`
}

/** "Fri 2 Oct" for a moment in time (the focus's start). */
export function day(unix: number): string {
  const x = new Date(unix * 1000)
  return `${DAYS[x.getDay()]} ${x.getDate()} ${MONTHS[x.getMonth()]}`
}

/** A measure's name, as a heading. */
export const MEASURE_LABEL: Record<Measure, string> = {
  fillers: 'Fillers per 100 words', questions: 'Questions per 10 minutes', talk_share: 'Your share of the talk',
  talked_over: 'Talking over someone, per 10 minutes', pauses: 'Hesitation pauses per 100 words',
  restarts: 'Restarts per 100 words', hedges: 'Hedges per 100 words', pace: 'Words per minute while you speak',
  longest: 'Longest stretch without a pause', patience: 'Your wait before you take the turn',
  turns: 'Changes of hands per minute', their_longest: 'Their longest stretch', pitch_range: 'Your pitch range',
  loudness: 'Your loudness against the others', pushed_pitch: 'Your pitch range when pushed back',
  pushed_loudness: 'Your loudness when pushed back',
}
/** What follows a measure's value in a sentence: "4.1 fillers per 100 words", "30% of the talk". */
export const MEASURE_UNIT: Record<Measure, string> = {
  fillers: 'fillers per 100 words', questions: 'questions per 10 minutes', talk_share: 'of the talk',
  talked_over: 'times talking over someone per 10 minutes', pauses: 'hesitation pauses per 100 words',
  restarts: 'restarts per 100 words', hedges: 'hedges per 100 words', pace: 'words a minute', longest: 'without a pause',
  patience: 'before you take the turn', turns: 'changes of hands a minute', their_longest: 'before someone took the floor',
  pitch_range: 'semitones of pitch range', loudness: 'dB against the others', pushed_pitch: 'semitones against the rest',
  pushed_loudness: 'dB against the rest',
}
export function measureValue(m: Measure, v: number): string {
  if (m === 'pace') return String(Math.round(v))
  if (m === 'longest' || m === 'their_longest') return v < 60 ? `${Math.round(v)} s` : mss(v)
  if (m === 'patience') return `${v.toFixed(1)} s`
  // A difference (B27): signed, so "+1.2 dB" and "−0.5 dB" read as louder and quieter than the others or the rest.
  if (m === 'loudness' || m === 'pushed_pitch' || m === 'pushed_loudness') {
    const r = Math.abs(v).toFixed(1)
    return r === '0.0' ? r : `${v > 0 ? '+' : '−'}${r}`
  }
  return m === 'talk_share' ? `${Math.round(v * 100)}%` : v.toFixed(1)
}

/** A focus's record so far, e.g. "did it 2 · missed 1"; empty before any answer. */
export function tallyText(t: { did: number; missed: number; na: number }): string {
  return [t.did && `did it ${t.did}`, t.missed && `missed ${t.missed}`, t.na && `didn't come up ${t.na}`].filter(Boolean).join(' · ')
}

/** A line as plain and marked pieces, by [start, end) character spans (fillers to underline). */
export function pieces(text: string, spans: [number, number][]): { t: string; on: boolean }[] {
  const out: { t: string; on: boolean }[] = []
  let at = 0
  for (const [a, b] of [...spans].sort((x, y) => x[0] - y[0])) {
    if (a < at) continue  // overlapping: keep the first
    if (a > at) out.push({ t: text.slice(at, a), on: false })
    out.push({ t: text.slice(a, b), on: true })
    at = b
  }
  if (at < text.length) out.push({ t: text.slice(at), on: false })
  return out
}

/** What a search found (B12): "No matches", "1 match", "200 of 340 shown". */
export function searchCount(r: { results: unknown[]; total: number }): string {
  if (!r.total) return 'No matches'
  if (r.results.length < r.total) return `${r.results.length} of ${r.total} shown: add a word or a filter to narrow it`
  return `${r.total} match${r.total === 1 ? '' : 'es'}`
}
