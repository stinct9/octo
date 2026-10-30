import { describe, expect, it } from 'vitest'
import { day, deviceLabel, searchCount, etaText, impactText, inProgress, measureValue, MEASURE_UNIT, mss, needsYou, pieces, today, tallyText, roughly, speakerColors, todoLabel, when } from './format'
import type { DeliveryRow, Device } from './types'
import { deliveryValue, focusText, spreadText, stateWord, usualText } from './delivery'
import { href, parse } from './router'
import { searchPath } from './api'

describe('format', () => {
  it('gives 8 speakers their own colour, then grey; the owner is always their', () => {
    const sp = 'ABCDEFGHIJ'.split('').map((letter) => ({ letter, self: letter === 'C' }))
    const c = speakerColors(sp)
    expect([c.A, c.B, c.C, c.D, c.J]).toEqual(['s1', 's2', 'me', 's3', 'sx'])
    expect(new Set(Object.values(c).filter((x) => x.startsWith('s') && x !== 'sx')).size).toBe(8)
  })

  it('dates from another year say the year; today is local', () => {
    const now = new Date(2026, 9, 5, 9, 0)
    expect(when(new Date(2022, 8, 30, 18, 2).getTime() / 1000, now)).toBe('Fri 30 Sep 2022, 18:02')
    expect(when(new Date(2026, 8, 29, 20, 30).getTime() / 1000, now)).toBe('Tue 29 Sep, 20:30')
    expect(today(new Date(2026, 9, 5, 0, 30))).toBe('2026-10-05')
  })

  it('mss', () => {
    expect(mss(39.3)).toBe('0:39')
    expect(mss(362)).toBe('6:02')
    expect(mss(3725)).toBe('1:02:05')
  })

  it('when is relative to today', () => {
    const now = new Date(2026, 9, 1, 22, 0)
    expect(when(new Date(2026, 9, 1, 21, 40).getTime() / 1000, now)).toBe('Today, 21:40')
    expect(when(new Date(2026, 8, 30, 8, 5).getTime() / 1000, now)).toBe('Yesterday, 08:05')
    expect(when(new Date(2026, 8, 29, 20, 30).getTime() / 1000, now)).toBe('Tue 29 Sep, 20:30')
    expect(when(null, now)).toBe('Unknown time')
  })

  it('the owner is always the accent; others take slots in order', () => {
    expect(speakerColors([{ letter: 'A', self: false }, { letter: 'B', self: true }, { letter: 'C', self: false }]))
      .toEqual({ A: 's1', B: 'me', C: 's2' })
  })

  it('focus day and record', () => {
    expect(day(new Date(2026, 9, 2, 14, 0).getTime() / 1000)).toBe('Fri 2 Oct')
    expect(tallyText({ did: 2, missed: 0, na: 1 })).toBe("did it 2 · didn't come up 1")
    expect(tallyText({ did: 0, missed: 0, na: 0 })).toBe('')
  })

  it('todo labels', () => {
    expect(todoLabel({ kind: 'review' })).toEqual({ text: 'Ready to review', tone: 'accent' })
    expect(todoLabel({ kind: 'paused', reason: 'In Kannada' }).text).toBe('In Kannada · not transcribed yet')
    expect(todoLabel({ kind: 'confirm' }).tone).toBe('warn')
    expect(todoLabel({ kind: 'not_you' })).toEqual({ text: 'Without you · numbers only', tone: 'done' })
  })

  it('needs something holds only what the owner can act on', () => {
    expect(needsYou({ kind: 'confirm' }) && needsYou({ kind: 'review' })).toBe(true)
    expect(needsYou({ kind: 'failed', stage: 'insight', error: 'x', auto_retry: false })).toBe(true)
    expect(needsYou({ kind: 'failed', stage: 'insight', error: 'limit', auto_retry: true })).toBe(false)
    expect(inProgress({ kind: 'failed', stage: 'insight', error: 'limit', auto_retry: true })).toBe(true)
    for (const t of [{ kind: 'not_you' }, { kind: 'reviewed' }, { kind: 'processing', stage: 'speakers', step: 2, of: 6 }] as const) {
      expect(needsYou(t)).toBe(false)
    }
    expect(todoLabel({ kind: 'failed', stage: 'insight', error: 'timed out', auto_retry: true }).text).toBe('Failed while reviewing the conversation · will try again')
    expect(todoLabel({ kind: 'processing', stage: 'speakers', step: 2, of: 6 }).text).toBe('Separating speakers… · step 2 of 6')
    expect(todoLabel({ kind: 'processing', stage: 'insight', step: 7, of: 7, queued: true,
      detail: "waiting for Claude's usage limit to reset (about 10:40)" }).text)
      .toBe("Queued: reviewing the conversation · waiting for Claude's usage limit to reset (about 10:40)")
    expect(todoLabel({ kind: 'short', reason: 'Nothing in this review to answer' }).text).toBe('Nothing in the review to answer')
    expect(todoLabel({ kind: 'short', reason: 'too short: you spoke for 12 s' }).text).toBe('Numbers only · too short for insights')
  })
})

describe('fillers in a line', () => {
  it('splits a line into plain and marked pieces', () => {
    expect(pieces('So, you know, um, fine', [[14, 16], [4, 12]])).toEqual([
      { t: 'So, ', on: false }, { t: 'you know', on: true }, { t: ', ', on: false }, { t: 'um', on: true }, { t: ', fine', on: false }])
    expect(pieces('plain', [])).toEqual([{ t: 'plain', on: false }])
  })
})

describe('measures', () => {
  it('says each number the way it reads in a sentence', () => {
    expect(`${measureValue('talk_share', 0.304)} ${MEASURE_UNIT.talk_share}`).toBe('30% of the talk')
    expect(`${measureValue('fillers', 4.15)} ${MEASURE_UNIT.fillers}`).toBe('4.2 fillers per 100 words')
    expect(`${measureValue('questions', 6)} ${MEASURE_UNIT.questions}`).toBe('6.0 questions per 10 minutes')
  })
})

describe('router', () => {
  it('parses hash routes and falls back to the list', () => {
    expect(parse('#/c/ses_20261001_72668866')).toEqual({ name: 'conversation', id: 'ses_20261001_72668866' })
    expect(parse('#/coach')).toEqual({ name: 'coach' })
    expect(parse('#/ask')).toEqual({ name: 'ask' })
    expect(parse('#/fillers')).toEqual({ name: 'fillers' })
    expect(parse('#/fillers/check')).toEqual({ name: 'fillercheck' })
    expect(parse('#/coach/coach_20261015_6186b4c4')).toEqual({ name: 'coach', note: 'coach_20261015_6186b4c4' })
    expect(parse('#/coach/../x')).toEqual({ name: 'conversations' })
    expect(parse('#/people/per_20261001_ab12cd34')).toEqual({ name: 'person', id: 'per_20261001_ab12cd34' })
    expect(parse('#/people/../x')).toEqual({ name: 'conversations' })
    expect(parse('#/people')).toEqual({ name: 'people' })
    expect(parse('#/bench')).toEqual({ name: 'bench' })
    expect(parse('#/phone')).toEqual({ name: 'phone' })
    expect(parse('#/mac')).toEqual({ name: 'mac' })  // This Mac, apart from Set up Octo (22 Oct)
    expect(parse('')).toEqual({ name: 'conversations' })
    expect(parse('#/c/../../etc')).toEqual({ name: 'conversations' })
  })
})

describe('search (B12)', () => {
  it('a result opens its conversation at the line; review text without a quote opens the conversation', () => {
    expect(href.line('ses_20261001_72668866', 12)).toBe('#/c/ses_20261001_72668866/12')
    expect(parse(href.line('ses_20261001_72668866', 12))).toEqual({ name: 'conversation', id: 'ses_20261001_72668866', line: 12 })
    expect(parse('#/c/ses_20261001_72668866/0')).toEqual({ name: 'conversation', id: 'ses_20261001_72668866', line: 0 })
    expect(href.line('ses_x', null)).toBe('#/c/ses_x')
    expect(parse('#/c/ses_x/1e3')).toEqual({ name: 'conversations' })
  })

  it('asks with only the filters that are set', () => {
    expect(searchPath('work load')).toBe('/api/octo/search?q=work+load')
    expect(searchPath('a&b', 'per_1', 'them')).toBe('/api/octo/search?q=a%26b&person=per_1&who=them')
  })

  it('says how much it found', () => {
    expect(searchCount({ results: [], total: 0 })).toBe('No matches')
    expect(searchCount({ results: [1], total: 1 })).toBe('1 match')
    expect(searchCount({ results: [1, 2], total: 5 })).toBe('2 of 5 shown: add a word or a filter to narrow it')
  })
})

describe('status', () => {
  it('says when a step is only waiting its turn', () => {
    expect(todoLabel({ kind: 'processing', stage: 'identify', step: 3, of: 5, queued: true }).text).toBe('Queued: recognising voices · step 3 of 5')
    expect(todoLabel({ kind: 'processing', stage: 'insight', step: 5, of: 5, detail: 'principle 2 of 14' }).text)
      .toBe('Reviewing the conversation… · principle 2 of 14')
  })
})

describe('recorder chip', () => {
  const now = 1_790_000_000
  const d = (x: Partial<Device>): Device => ({
    configured: true, seen_at: now - 60, checked_at: now - 60, error: null, syncing: false, state: 'recording', want_on: true,
    card_hours_left: 270, battery_mv: null, rssi: -50, sd_failures: 0, dropped_samples: 0, uptime_h: 1, waiting_on_mac: 0, ...x,
  })
  it('is hidden until a recorder is set up', () => expect(deviceLabel(d({ configured: false }), now)).toBeNull())
  it('says what it is doing when it answered the last check', () => {
    expect(deviceLabel(d({}), now)).toMatchObject({ text: 'Online · Recording', tone: 'rec', home: true })
    expect(deviceLabel(d({ state: 'off' }), now)).toMatchObject({ text: 'Online · Not recording', tone: 'ok' })
    expect(deviceLabel(d({ state: 'card_full' }), now)).toMatchObject({ text: 'Online · card full', tone: 'fail' })
  })
  it('stops claiming online once checks go unanswered for a few minutes', () => {
    expect(deviceLabel(d({ checked_at: now - 5 * 60, seen_at: now - 5 * 60 }), now)?.text).toMatch(/^Offline · seen /)
  })
  it('is away when the last check failed, without claiming it stopped', () => {
    const away = deviceLabel(d({ checked_at: now - 30, seen_at: now - 7200, error: 'not reachable' }), now)
    expect(away?.home).toBe(false)
    expect(away?.text).toMatch(/^Offline · seen /)
  })
  it('warns once it has been away longer than a day out, unless it was switched off', () => {
    const long = { checked_at: now - 30, seen_at: now - 20 * 3600, error: 'not reachable' }
    expect(deviceLabel(d({ ...long, want_on: true }), now)?.tone).toBe('warn')
    expect(deviceLabel(d({ ...long, want_on: false }), now)?.tone).toBe('muted')
    expect(deviceLabel(d({ checked_at: now - 30, seen_at: now - 9 * 3600, error: 'x', want_on: true }), now)?.tone).toBe('muted')
  })
})

describe('pipeline switch wording', () => {
  it('says what is redone and roughly how long', () => {
    expect(impactText({ conversations: 11, later: ['Separate speakers', 'Recognise voices', 'Count'], estimate_s: 6165, estimate_partial: false }))
      .toBe('Redoes this step for 11 conversations, then separate speakers, recognise voices and count. Roughly 1½ h, in the background.')
    expect(impactText({ conversations: 1, later: [], estimate_s: null, estimate_partial: true }))
      .toBe('Redoes this step for 1 conversation.')
    expect(impactText({ conversations: 0, later: [], estimate_s: null, estimate_partial: false }))
      .toBe('Nothing to redo yet: new conversations will use it.')
    expect(roughly(40 * 60)).toBe('about 40 min')
    expect(roughly(2 * 3600)).toBe('about 2 h')
  })
})

describe('when it will be ready (B3)', () => {
  const now = new Date(2026, 9, 13, 14, 0).getTime()
  const at = (min: number) => (now + min * 60_000) / 1000
  it('says it the way a person would', () => {
    expect(etaText(at(0.5), false, now)).toBe('any moment')
    expect(etaText(at(3.2), false, now)).toBe('in about 4 min')
    expect(etaText(at(22), false, now)).toBe('in about 25 min')
    expect(etaText(at(80), false, now)).toBe('about 15:20')
    expect(etaText(at(60 * 20), false, now)).toBe('about Wed 10:00')
  })
  it('never gives a time already past', () => {
    expect(etaText(at(-5), false, now)).toBe('any moment')
    expect(etaText(at(30), true, now)).toBe('taking longer than usual')
    expect(etaText(null, false, now)).toBeNull()
  })
  it('the list says when, not which step', () => {
    expect(todoLabel({ kind: 'processing', stage: 'transcribe', step: 1, of: 6, queued: true, eta: Date.now() / 1000 + 200, then: 'you' }).text)
      .toBe('Ready for you in about 4 min')
    expect(todoLabel({ kind: 'processing', stage: 'insight', step: 6, of: 6, queued: true, eta: Date.now() / 1000 + 30, then: 'review',
      detail: "waiting for Claude's usage limit to reset (about 16:00)" }).text)
      .toBe("Review any moment · waiting for Claude's usage limit to reset (about 16:00)")
  })
})

describe('how you showed up (B10)', () => {
  const row = (over: Partial<DeliveryRow>): DeliveryRow => ({ measure: 'fillers', value: 3.1, usual: 4.6, lo: 4, hi: 5, state: 'low',
    conversations: 12, groups_higher: false, ...over })
  it('says where the number sits in his own words for each measure, never better or worse', () => {
    expect(stateWord(row({}))).toBe('fewer than usual')
    expect(stateWord(row({ measure: 'pace', state: 'high' }))).toBe('faster than usual')
    expect(stateWord(row({ measure: 'longest', state: 'low' }))).toBe('shorter than usual')
    expect(stateWord(row({ state: 'about' }))).toBe('about usual')
    expect(stateWord(row({ state: 'short' }))).toBe('too short to tell')
    expect(stateWord(row({ state: 'no_usual', usual: null }))).toBe('no usual yet')
    const all = (['low', 'about', 'high', 'short', 'no_usual', 'off', 'unmeasured'] as const)
      .flatMap((st) => (['fillers', 'pace', 'longest'] as const).map((m) => stateWord(row({ measure: m, state: st }))))
    expect(all.filter((w) => /better|worse|good|bad/.test(w))).toEqual([])
  })
  it('gives the number, the usual and the spread in each measure\'s units', () => {
    expect(deliveryValue('fillers', 3.14)).toBe('3.1 per 100 words')
    expect(deliveryValue('pace', 151.6)).toBe('152 words a minute')
    expect(deliveryValue('longest', 42.3)).toBe('42 s without a pause')
    expect(deliveryValue('longest', 95)).toBe('1:35 without a pause')
    expect(deliveryValue('pauses', null)).toBe('–')
    expect(usualText(row({}))).toBe('usually 4.6')
    expect(usualText(row({ usual: null }))).toBe('')
    expect(spreadText(row({}))).toBe('About usual is 4.0–5.0: the middle half of your 12 conversations before this.')
    expect(focusText('pauses')).toBe('Watch my hesitation pauses')
  })
})

describe('listening (B13)', () => {
  const row = (over: Partial<DeliveryRow>): DeliveryRow => ({ measure: 'patience', value: 1.2, usual: 0.8, lo: 0.5, hi: 1, state: 'high',
    conversations: 9, groups_higher: false, ...over })
  it('says each in its own neutral words, seconds for waits and stretches', () => {
    expect(stateWord(row({}))).toBe('longer than usual')
    expect(stateWord(row({ measure: 'turns', state: 'low' }))).toBe('fewer than usual')
    expect(stateWord(row({ measure: 'their_longest', state: 'about' }))).toBe('about usual')
    expect(deliveryValue('patience', 0)).toBe('0.0 s before you speak')
    expect(deliveryValue('turns', 4.26)).toBe('4.3 changes a minute')
    expect(deliveryValue('their_longest', 95)).toBe('1:35 without being cut off')
    expect(usualText(row({}))).toBe('usually 0.8 s')
    expect(spreadText(row({}))).toBe('About usual is 0.5 s–1.0 s: the middle half of your 9 conversations before this.')
    const all = (['low', 'about', 'high'] as const)
      .flatMap((st) => (['patience', 'turns', 'their_longest'] as const).map((m) => stateWord(row({ measure: m, state: st }))))
    expect(all.filter((w) => /better|worse|good|bad|too/.test(w))).toEqual([])
  })
  it('starts a focus in words that carry no direction', () => {
    expect(focusText('patience')).toBe('Watch my patience')
    expect(focusText('turns')).toBe('Watch my turn-taking')
    expect(focusText('their_longest')).toBe('Let them finish')
  })
})

describe('voice (B27)', () => {
  const row = (over: Partial<DeliveryRow>): DeliveryRow => ({ measure: 'pitch_range', value: 9.6, usual: 7.1, lo: 6.2, hi: 7.5,
    state: 'high', conversations: 24, groups_higher: false, ...over })
  it('says narrower / wider and quieter / louder, never flat or tense', () => {
    expect(stateWord(row({}))).toBe('wider than usual')
    expect(stateWord(row({ measure: 'loudness', state: 'low' }))).toBe('quieter than usual')
    expect(stateWord(row({ measure: 'pushed_pitch', state: 'about' }))).toBe('about usual')
    expect(stateWord(row({ state: 'no_audio' }))).toBe('audio deleted')
    const all = (['low', 'about', 'high'] as const).flatMap((st) => (['pitch_range', 'loudness', 'pushed_pitch', 'pushed_loudness'] as const)
      .map((m) => stateWord(row({ measure: m, state: st }))))
    expect(all.filter((w) => /flat|tense|monoton|better|worse|good|bad|too/.test(w))).toEqual([])
  })
  it('gives a difference its sign', () => {
    expect(deliveryValue('pitch_range', 7.14)).toBe('7.1 semitones')
    expect(deliveryValue('loudness', 1.24)).toBe('+1.2 dB against the others')
    expect(deliveryValue('loudness', -0.47)).toBe('−0.5 dB against the others')
    expect(deliveryValue('pushed_loudness', 0)).toBe('0.0 dB against the rest')
    expect(deliveryValue('pushed_pitch', null)).toBe('–')
    expect(deliveryValue('pushed_pitch', -0.04)).toBe('0.0 semitones against the rest')
    expect(usualText(row({ measure: 'loudness', usual: -0.5 }))).toBe('usually −0.5')
  })
  it('starts a focus in words that carry no direction', () => {
    expect(focusText('pitch_range')).toBe('Watch my pitch range')
    expect(focusText('pushed_loudness')).toBe('Watch my loudness when pushed back')
  })
})
