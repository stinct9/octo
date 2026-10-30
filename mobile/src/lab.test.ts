import { describe, expect, it, vi } from 'vitest'
import { FakeLabNative, Lab, isTailnet, parseLink } from './lab'
import type { LabConfig, LabNative, LabStore } from './lab'

const mem = (c: LabConfig | null = null): LabStore & { c: LabConfig | null } => ({ c, async load() { return this.c }, async save(x) { this.c = x } })
const LINK = `octo://pair?v=1&n=Sam%27s%20MacBook&h=sams-mac.local,192.168.1.20,100.101.102.103&p=8766&ca=${'ab'.repeat(32)}&s=Xy-z_1`

describe('the code from the Mac', () => {
  it('reads what octo/phone.py pair_link writes', () => {
    expect(parseLink(LINK)).toEqual({ mac: 'Sam\'s MacBook', hosts: ['sams-mac.local', '192.168.1.20', '100.101.102.103'], port: 8766, ca: 'ab'.repeat(32), secret: 'Xy-z_1' })
  })
  it('refuses anything else', () => {
    expect(parseLink('https://example.com')).toBeNull()
    expect(parseLink(LINK.replace('v=1', 'v=2'))).toBeNull()
    expect(parseLink(LINK.replace(/ca=[0-9a-f]+/, 'ca=xyz'))).toBeNull()
    expect(parseLink(LINK.replace(/&s=[^&]+/, ''))).toBeNull()
  })
  it('knows a Tailscale address', () => {
    expect(isTailnet('100.101.102.103')).toBe(true)
    expect(isTailnet('100.64.0.1')).toBe(true)
    expect(isTailnet('100.128.0.1')).toBe(false)
    expect(isTailnet('100.63.0.1')).toBe(false)
    expect(isTailnet('192.168.1.20')).toBe(false)
  })
})

describe('the Lab tab', () => {
  it('pairs from a code, then reaches the Mac and follows its addresses', async () => {
    const store = mem()
    const lab = new Lab(new FakeLabNative(), store)
    await lab.load()
    expect(lab.s.phase).toBe('none')
    await lab.pairWith(LINK)
    expect(lab.s.phase).toBe('ready')
    expect(lab.url).toBe('https://192.168.1.20:8766/')
    expect(lab.away).toBe(false)
    expect(store.c?.mac).toBe('Sam\'s MacBook (demo)')
    expect(lab.hasTailscale).toBe(true)
  })
  it('keeps the Tailscale address while the Mac\'s Tailscale is off, and follows a new one', async () => {
    // 13 Oct: reached at home with the Mac's Tailscale off, the app dropped 100.x and couldn't find the Mac away.
    const native = new FakeLabNative()
    const store = mem()
    const lab = new Lab(native, store)
    await lab.pairWith(LINK)
    const said = (tailscale: string[]) => {
      const home = ['sams-mac.local', '192.168.1.20']
      native.hello = async () => ({ host: '192.168.1.20', status: 200, paired: true, hello: { mac: 'Mac', hosts: [...home, ...tailscale], home, tailscale, port: 8766, device: 'iPhone', ca_sha256: 'a'.repeat(64) } })
    }
    said([])
    await lab.reach()
    expect(store.c?.hosts).toContain('100.101.102.103')
    said(['100.80.0.9'])
    await lab.reach()
    expect(store.c?.hosts).toContain('100.80.0.9')
    expect(store.c?.hosts).not.toContain('100.101.102.103')
  })
  it('a Mac that doesn\'t answer, and a phone that was removed', async () => {
    const native = new FakeLabNative()
    const lab = new Lab(native, mem())
    await lab.pairWith(LINK)
    native.reachable = false
    await lab.reach()
    expect(lab.s.phase).toBe('unreachable')
    native.reachable = true
    native.paired = false  // unpaired on the Mac
    await lab.reach()
    expect(lab.s.phase).toBe('removed')
  })
  it('looks again on its own for a minute when the Mac doesn\'t answer (14 Oct: Tailscale came up after the one look)', async () => {
    vi.useFakeTimers()
    try {
      const native = new FakeLabNative()
      native.paired = true
      native.reachable = false
      const lab = new Lab(native, mem({ mac: 'Mac', hosts: ['100.101.102.103'], port: 8766, ca: 'ab'.repeat(32), pairedAt: 1 }))
      const looks = vi.spyOn(native, 'hello')
      const loading = lab.load()
      await vi.advanceTimersByTimeAsync(600)
      await loading
      expect(lab.s.phase).toBe('unreachable')
      await vi.advanceTimersByTimeAsync(5600)
      expect(looks).toHaveBeenCalledTimes(2)
      expect(lab.s.phase).toBe('unreachable')  // the screen stays put while it looks
      native.reachable = true
      await vi.advanceTimersByTimeAsync(5600)
      expect(lab.s.phase).toBe('ready')
      await vi.advanceTimersByTimeAsync(60_000)
      expect(looks).toHaveBeenCalledTimes(3)  // found: no more looking

      // and it gives up after a minute, until the app comes back to the front or Try again
      native.reachable = false
      void lab.reach()
      await vi.advanceTimersByTimeAsync(600 + 12 * 5600 + 60_000)
      expect(looks).toHaveBeenCalledTimes(3 + 1 + 12)
    } finally {
      vi.useRealTimers()
    }
  })
  it('says why a code didn\'t work', async () => {
    const native = new FakeLabNative()
    native.pair = async () => { throw new Error('code used or expired') }
    const lab = new Lab(native, mem())
    await lab.pairWith(LINK)
    expect(lab.s.phase).toBe('pair_failed')
    expect(lab.s.failure).toMatch(/used or ran out/)
    await lab.pairWith('not a code')
    expect(lab.s.failure).toMatch(/isn't a code/)
  })
})

describe('a review is ready (the phone\'s own notifications)', () => {
  it('asks only once the lab is reached, and remembers the answer', async () => {
    const native = new FakeLabNative()
    native.paired = true
    const lab = new Lab(native, mem({ mac: 'Mac', hosts: ['192.168.1.20'], port: 8766, ca: 'a'.repeat(64), pairedAt: 1 }))
    expect(lab.s.notify).toBeNull()  // nothing asked before the lab is there
    await lab.load()
    expect(lab.s.phase).toBe('ready')
    expect(lab.s.notify?.state).toBe('ask')
    await lab.allowNotifications()
    expect(lab.s.notify?.state).toBe('on')
    await lab.notNow()
    expect(lab.s.notify?.state).toBe('off')
  })

  it('a phone that can\'t says so instead of failing the tab', async () => {
    const native = new FakeLabNative()
    native.paired = true
    native.notifications = async () => { throw new Error('not implemented') }
    const lab = new Lab(native, mem({ mac: 'Mac', hosts: ['192.168.1.20'], port: 8766, ca: 'a'.repeat(64), pairedAt: 1 }))
    await lab.load()
    expect(lab.s.phase).toBe('ready')
    expect(lab.s.notify?.state).toBe('unsupported')
  })
})

describe('the link instead of the camera', () => {
  it('pastes the link copied on the Mac and pairs as a scan would', async () => {
    const store = mem()
    const lab = new Lab(new FakeLabNative(), store)
    await lab.load()
    await lab.paste()
    expect(lab.s.phase).toBe('ready')
    expect(store.c?.hosts).toContain('192.168.1.20')
  })

  it('says where to copy it when there is nothing to paste', async () => {
    const native = new FakeLabNative()
    native.paste = async () => ({ text: '' })
    const lab = new Lab(native, mem())
    await lab.load()
    await lab.paste()
    expect(lab.s.phase).toBe('none')
    expect(lab.s.failure).toMatch(/Copy the link/)
  })
})

describe('what reaches the native side', () => {
  it('is plain data, never a Vue proxy (one arrives in Swift as nothing: 8 Oct, "Can\'t reach your Mac")', async () => {
    const { isProxy } = await import('vue')
    const native = new FakeLabNative()
    native.paired = true
    const seen: unknown[] = []
    const hello = native.hello.bind(native)
    ;(native as LabNative).hello = async (o) => { seen.push(o.hosts); return hello() }
    ;(native as LabNative).show = async (o) => { seen.push(o.hosts, o.rect) }
    const lab = new Lab(native, mem({ mac: 'Mac', hosts: ['192.168.1.20'], port: 8766, ca: 'a'.repeat(64), pairedAt: 1 }))
    await lab.load()
    await lab.reach()
    await lab.show({ x: 0, y: 0, width: 10, height: 10 })
    expect(seen.length).toBeGreaterThan(2)
    expect(seen.some((v) => isProxy(v))).toBe(false)
  })
})
