import { describe, expect, it, vi } from 'vitest'
import { FakeLabNative, Lab } from './lab'
import type { LabConfig, LabStore } from './lab'
import { FakeMemosNative, Memos, isFile } from './memos'
import type { CueSettings, CueStore } from './memos'

const CFG: LabConfig = { mac: 'Mac', hosts: ['192.168.1.20'], port: 8766, ca: 'ab'.repeat(32), pairedAt: 1 }
const mem = (c: LabConfig | null): LabStore => ({ async load() { return c }, async save() {} })

async function setup(reachable = true) {
  const labNative = new FakeLabNative()
  labNative.paired = true
  labNative.reachable = reachable
  ;(labNative as unknown as { wait: () => Promise<void> }).wait = async () => {}
  const lab = new Lab(labNative, mem(CFG))
  await lab.load()
  const native = new FakeMemosNative()
  const saved: { cue: CueSettings | null } = { cue: null }
  const cueStore: CueStore = { async load() { return saved.cue }, async save(c) { saved.cue = c } }
  const memos = new Memos(native, lab, cueStore)
  await memos.load()
  return { lab, labNative, native, memos, saved }
}

describe('memos from the phone', () => {
  it('a shared Voice Memo goes to the Mac and leaves the phone', async () => {
    const { native, memos } = await setup()
    await memos.take('file:///var/mobile/Containers/Data/Application/X/Documents/Inbox/Team%20sync.m4a')
    expect(native.items).toEqual([])
    expect(memos.s.sent).toBe(1)
    expect(memos.s.items).toEqual([])
  })
  it('refuses a file that isn\'t audio', async () => {
    const { native, memos } = await setup()
    await memos.take('file:///tmp/notes.pdf')
    expect(native.items).toEqual([])
    expect(memos.s.failure).toMatch(/isn't an audio file/)
  })
  it('waits on the phone while the Mac doesn\'t answer, and goes once it does', async () => {
    const { lab, labNative, native, memos } = await setup(false)
    expect(lab.s.phase).toBe('unreachable')
    await memos.start()
    expect(memos.s.recording).toBe(true)
    await memos.stop()
    expect(memos.s.recording).toBe(false)
    expect(memos.s.items.map((x) => x.name)).toEqual(['Octo recording.m4a'])  // kept: nowhere to send it
    labNative.reachable = true
    await lab.reach()
    await memos.send()  // App.vue does this when the lab becomes ready
    expect(native.items).toEqual([])
    expect(memos.s.sent).toBe(1)
  })
  it('a file the Mac already has leaves too; one it refuses stays, with why', async () => {
    const { native, memos } = await setup()
    native.answer = 409
    await memos.take('file:///x/Documents/Inbox/a.m4a')
    expect(native.items).toEqual([])
    native.answer = 415
    await memos.take('file:///x/Documents/Inbox/b.m4a')
    expect(memos.s.items).toHaveLength(1)
    expect(memos.s.errors[memos.s.items[0].id]).toMatch(/can't read/)
    await memos.remove(memos.s.items[0].id)
    expect(memos.s.items).toEqual([])
  })
  it('stops at a Mac that stops answering, keeping the rest', async () => {
    const { native, memos } = await setup()
    native.answer = 0
    await memos.take('file:///x/Documents/Inbox/a.m4a')
    await memos.take('file:///x/Documents/Inbox/b.m4a')
    expect(memos.s.items).toHaveLength(2)
    expect(memos.s.errors).toEqual({})
  })
  it('tells a shared file from the pairing link', () => {
    expect(isFile('file:///x/a.m4a')).toBe(true)
    expect(isFile('octo://pair?v=1')).toBe(false)
    expect(isFile(undefined)).toBe(false)
  })
  it('marks a moment on the Mac, and says so when the Mac isn\'t there (B16)', async () => {
    const { lab, labNative, native, memos } = await setup()
    expect(await memos.mark()).toBe(true)
    expect(native.marks).toBe(1)
    expect(memos.s.markedAt).toBeGreaterThan(0)
    labNative.reachable = false
    await lab.reach()
    expect(await memos.mark()).toBe(false)
    expect(native.marks).toBe(1)  // nothing kept for later: the Mac's clock places a mark
    expect(memos.s.markError).toMatch(/wasn't marked/)
  })
  it('takes back the mark just made, once (Undo)', async () => {
    const { native, memos } = await setup()
    expect(await memos.unmark()).toBe(false)  // nothing to undo yet
    await memos.mark()
    expect(await memos.unmark()).toBe(true)
    expect(native.marks).toBe(0)
    expect(memos.s.markedAt).toBe(0)
    expect(await memos.unmark()).toBe(false)  // only the one just made
  })

  it('the live filler cue (B21): off until turned on, the list kept from the Mac, the filler then the count', async () => {
    vi.useFakeTimers()
    const { native, memos, saved } = await setup()
    await memos.start()
    expect(native.cue).toBeUndefined()  // off by default: the memo starts as before
    await memos.stop()
    await memos.setCue(true)
    expect(saved.cue).toEqual({ on: true, haptic: false, phrases: ['um', 'uh', 'you know', 'kind of'] })
    native.phrases = ['um']
    await memos.setCue(true, true)  // the list isn't fetched again when it has one: it's refreshed when the Mac answers
    await memos.start()
    expect(native.cue).toEqual({ cue: ['um', 'uh', 'you know', 'kind of'], haptic: true })
    native.hear('you know')
    expect(memos.s.cue.flash).toBe('you know')
    native.hear('uh', true)  // late: counted, not flashed
    expect(memos.s.cue).toMatchObject({ flash: 'you know', count: 2 })
    vi.advanceTimersByTime(1500)
    expect(memos.s.cue.flash).toBe('')
    await memos.fetchPhrases()
    expect(saved.cue?.phrases).toEqual(['um'])
    vi.useRealTimers()
  })
  it('the cue stays off on a phone that can\'t recognise speech on itself', async () => {
    const { native, memos } = await setup()
    native.cueAvailable = false
    await memos.load()
    await memos.setCue(true)
    await memos.start()
    expect(memos.s.cue.available).toBe(false)
    expect(native.cue).toBeUndefined()
  })
})
