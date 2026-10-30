import { describe, expect, it } from 'vitest'
import realRun from '../__fixtures__/run-all-pass.json'
import type { RunEvent } from '../types'
import { reduce, replay } from './reduce'
import { wer } from './format'

const events = realRun as RunEvent[]

describe('reduce, on a real run-all from the board (2026-09-30 14:44)', () => {
  const run = replay(events)!

  it('ends idle with every step passed', () => {
    expect(run.state).toBe('idle')
    expect(run.status).toBe('pass')
    expect(run.steps.map((s) => [s.id, s.state])).toEqual([
      ['board', 'pass'], ['mic', 'pass'], ['sd', 'pass'], ['wifi', 'pass'], ['transcribe', 'pass'],
    ])
  })

  it('keeps metrics, levels, the recording and transcripts', () => {
    const sd = run.steps.find((s) => s.id === 'sd')!
    expect(sd.metrics.find((m) => m.key === 'headroom')?.verdict).toBe('pass')
    const mic = run.steps.find((s) => s.id === 'mic')!
    expect(mic.levels?.levels).toHaveLength(40)
    expect(mic.levels?.cue_at).toBe(3.2)
    expect(mic.recording).toBe('20260930-144400')
    const tr = run.steps.find((s) => s.id === 'transcribe')!
    expect(tr.transcripts?.[0].text).toBe('Seven green bottles are standing on the wall of the bench.')
  })

  it('clears the recording prompt once the step finishes', () => {
    expect(run.prompt).toBeNull()
    expect(run.recordingAt).toBeNull()
  })

  it('is idempotent when the server replays events after a reconnect', () => {
    const twice = replay([...events, ...events.slice(5)])!
    expect(twice.steps.map((s) => s.logs.length)).toEqual(run.steps.map((s) => s.logs.length))
    expect(twice.lastSeq).toBe(run.lastSeq)
  })

  it('tracks the human gate and the recording clock', () => {
    const upTo = (pred: (e: RunEvent) => boolean) => replay(events.slice(0, events.findIndex(pred) + 1))!
    const waiting = upTo((e) => e.type === 'step.prompt' && e.data.phase === 'ready')
    expect(waiting.state).toBe('waiting_for_user')
    expect(waiting.prompt?.step).toBe('mic')
    const speaking = upTo((e) => e.type === 'step.prompt' && e.data.phase === 'speak')
    expect(speaking.prompt?.phase).toBe('speak')
    expect(speaking.recordingAt).toBeGreaterThan(0)
    expect(speaking.prompt!.at - speaking.recordingAt!).toBeCloseTo(3, 0)
  })
})

describe('reduce, edge cases', () => {
  const started: RunEvent = {
    seq: 1, run_id: 'r1', t: 100, type: 'run.started', step: null,
    data: { steps: [{ id: 'sd', title: 'SD card', needs_user: false }] },
  }

  it('ignores events from a run it never saw start', () => {
    const run = replay([started])
    const stray: RunEvent = { seq: 9, run_id: 'other', t: 1, type: 'step.started', step: 'sd', data: {} }
    expect(reduce(run, stray)!.steps[0].state).toBe('queued')
  })

  it('marks unfinished steps aborted when the server restarted mid-run', () => {
    const run = replay([
      started,
      { seq: 2, run_id: 'r1', t: 101, type: 'step.started', step: 'sd', data: {} },
      { seq: 3, run_id: 'r1', t: 102, type: 'run.finished', step: null, data: { status: 'aborted', reason: 'server stopped mid-run' } },
    ])!
    expect(run.steps[0].state).toBe('aborted')
    expect(run.status).toBe('aborted')
  })

  it('level events never move the resume point', () => {
    const run = replay([started])!
    reduce(run, { seq: 0, run_id: 'r1', t: 101, type: 'step.level', step: 'mic', data: { dbfs: -50 } })
    expect(run.level).toBe(-50)
    expect(run.lastSeq).toBe(1)
  })
})

describe('word error rate', () => {
  it('scores substitutions, insertions and deletions', () => {
    expect(wer('Testing the Xiao board for voice quality', 'Testing the shower board for voice quality')).toEqual(
      { errors: 1, words: 7, rate: 1 / 7 })
    expect(wer("I'm testing the mic", "I'm testing the mic")!.rate).toBe(0)
    expect(wer('a b c', 'a c')!.errors).toBe(1)
    expect(wer('a b', 'a x b')!.errors).toBe(1)
    expect(wer('', 'anything')).toBeNull()
  })
})
