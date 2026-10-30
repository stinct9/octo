import { describe, expect, it } from 'vitest'
import { Connection, MemoryStore } from './connection'
import type { Phase } from './connection'
import { FakeLink } from './fake'

const FAST = { attemptMs: 40, firstReadMs: 200, codeAfterMs: 10, searchMs: 300, moreMs: 20, backoff: [0, 5, 10] }
const fake = (o: Partial<FakeLink> = {}) => Object.assign(new FakeLink({ call: 1, code: 30, restart: 60, updateStep: 2 }), o)

async function until(c: Connection, ok: (p: Phase) => boolean, ms = 2000) {
  const end = Date.now() + ms
  while (!ok(c.s.phase)) {
    if (Date.now() > end) throw new Error(`still ${c.s.phase}`)
    await new Promise((r) => setTimeout(r, 2))
  }
}

describe('the first time', () => {
  it('finds the recorder, connects, asks for the code once and remembers it', async () => {
    const link = fake()
    const store = new MemoryStore()
    const c = new Connection(link, store, FAST)
    await c.start()
    expect(c.s.phase).toBe('hello')
    await c.search()
    expect(c.s.phase).toBe('found')
    expect(c.s.found[0].name).toBe('Octo 1A2B')
    const p = c.choose(c.s.found[0])
    await until(c, (ph) => ph === 'code')  // the read waits for the person to type it
    await p
    expect(c.s.phase).toBe('connected')
    expect(c.s.event?.kind).toBe('connected_first')
    expect(store.r).toMatchObject({ id: 'fake', name: 'Octo 1A2B', fw: 'rec-15', boot: 45 })
    expect(c.s.nets).toEqual(['Home'])
  })

  it('says nothing was found when no recorder answers', async () => {
    const c = new Connection(fake({ nearby: false }), new MemoryStore(), FAST)
    await c.start()
    await c.search()
    expect(c.s.phase).toBe('cant_find')
  })

  it('waits a minute for the code, not five seconds (the plugin default)', async () => {
    const link = fake()
    link.times.code = 120  // slower than 5 s scaled; within the first read's time
    const c = new Connection(link, new MemoryStore(), FAST)
    await c.start()
    await c.search()
    await c.choose(c.s.found[0])
    expect(c.s.phase).toBe('connected')
  })

  it('a cancelled code box is a failed pairing, not "out of range"', async () => {
    const c = new Connection(fake({ codeAnswer: 'cancel' }), new MemoryStore(), FAST)
    await c.start()
    await c.search()
    await c.choose(c.s.found[0])
    expect(c.s.phase).toBe('pair_failed')
    expect(c.s.failure).toBe('auth')
  })
})

describe('a recorder it knows', () => {
  async function paired(o: Partial<FakeLink> = {}) {
    const link = fake({ bonded: true, ...o })
    const store = new MemoryStore({ id: 'fake', name: 'Octo 1A2B', fw: 'rec-15', boot: 45 })
    const c = new Connection(link, store, FAST)
    await c.start()
    await until(c, (p) => p === 'connected')
    return { link, store, c }
  }

  it('connects on launch with no search and no code', async () => {
    const { c } = await paired()
    expect(c.s.status?.state).toBe('recording')
  })

  it('reconnects by itself after a drop, never back to the find screen', async () => {
    const { link, c } = await paired()
    const seen: Phase[] = []
    link.setNearby(false)
    seen.push(c.s.phase)
    expect(c.s.why).toBe('lost')
    setTimeout(() => link.setNearby(true), 50)
    await until(c, (p) => { seen.push(p); return p === 'connected' })
    expect(seen).not.toContain('hello')
    expect(seen).not.toContain('searching')
    expect(c.s.event?.kind).toBe('reconnected')
  })

  it('after a restart (the reset button) it comes back by itself', async () => {
    const { link, c } = await paired()
    await link.restart()
    await until(c, (p) => p === 'connected')
    expect(c.s.status?.boot).toBe(46)
  })

  it('follows an update: the percent, the restart, then "updated" with the new version', async () => {
    const { link, store, c } = await paired()
    const pct: number[] = []
    const watch = setInterval(() => { if (c.s.status?.upd !== undefined && c.s.status.upd >= 0) pct.push(c.s.status.upd) }, 1)
    // The real recorder notifies every 2 s; here the update pushes each step.
    const done = link.update('rec-16')
    await until(c, (p) => p === 'reconnecting')
    expect(c.s.why).toBe('update')
    await done
    await until(c, (p) => p === 'connected')
    clearInterval(watch)
    expect(pct.length).toBeGreaterThan(3)
    // "Updated" once the Mac has confirmed the new version (the recorder's "Checking" first).
    const end = Date.now() + 1000
    while (c.s.event?.kind !== 'updated' && Date.now() < end) await new Promise((r) => setTimeout(r, 5))
    expect(c.s.status?.pending).toBe(false)
    expect(c.s.event).toMatchObject({ kind: 'updated', fw: 'rec-16', from: 'rec-15' })
    expect(store.r?.fw).toBe('rec-16')
  })

  it('says so when an update went back', async () => {
    const { link, c } = await paired()
    await link.update('rec-16', 'rolled_back')
    await until(c, (p) => p === 'connected')
    expect(c.s.event).toMatchObject({ kind: 'rolled_back', fw: 'rec-15' })
  })

  it('an older recorder (no update progress) is still followed: reconnected, then "updated"', async () => {
    const { link, c } = await paired({ version: 1 })
    await link.update('rec-16')
    await until(c, (p) => p === 'connected')
    expect(c.s.event).toMatchObject({ kind: 'updated', fw: 'rec-16' })  // no "pending" before rec-15: at once
  })

  it('says the recorder forgot this phone, instead of asking for a code that cannot work', async () => {
    const { link, c } = await paired()
    link.forgotPhone = true
    link.setNearby(false)
    link.setNearby(true)
    await until(c, (p) => p === 'forgotten')
  })

  it('stops trying in the background and tries again in the front', async () => {
    const { link, c } = await paired()
    c.background()
    link.setNearby(false)
    expect(c.s.phase).toBe('reconnecting')
    link.setNearby(true)
    await new Promise((r) => setTimeout(r, 40))
    expect(c.s.phase).toBe('reconnecting')  // not while in the background
    await c.foreground()
    await until(c, (p) => p === 'connected')
  })

  it('waits while Bluetooth is off and carries on when it is back', async () => {
    const { link, c } = await paired()
    link.setBluetooth(false)
    expect(c.s.phase).toBe('bluetooth_off')
    link.setBluetooth(true)
    await until(c, (p) => p === 'connected')
  })

  it('forget: back to the first run, and nothing kept', async () => {
    const { store, c } = await paired()
    await c.forget()
    expect(c.s.phase).toBe('hello')
    expect(store.r).toBeNull()
  })

  it('recording on and off, and the event for the panel', async () => {
    const { c } = await paired()
    await c.setRecording(false)
    expect(c.s.status?.state).toBe('off')
    expect(c.s.event?.kind).toBe('stopped')
    await c.setRecording(true)
    expect(c.s.status?.state).toBe('recording')
  })

  it('the filler cue (rec-24): switched from the phone, and absent on a recorder without it', async () => {
    const { c } = await paired({ fillerOn: false })
    expect(c.s.status?.filler_model).toBe(true)
    await c.setFiller(true)
    expect(c.s.status?.filler_on).toBe(true)
    await c.setFiller(false)
    expect(c.s.status?.filler_on).toBe(false)
    const old = await paired()
    expect(old.c.s.status?.filler_model).toBeUndefined()
  })
})
