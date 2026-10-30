// The lab inside the app (docs/phone-plan.md §4): pair once from the QR code the Mac shows, then find the
// Mac wherever it answers (the home Wi-Fi, or Tailscale away from home) and show the lab's own pages in a
// native web view that trusts only that Mac (ios/App/App/Lab.swift).
import { reactive } from 'vue'
import { Capacitor, registerPlugin } from '@capacitor/core'
import type { PluginListenerHandle } from '@capacitor/core'
import { Preferences } from '@capacitor/preferences'

export interface Rect { x: number; y: number; width: number; height: number }

export interface Hello {
  mac: string
  hosts: string[]
  home: string[]
  tailscale: string[]
  port: number
  device: string | null
  ca_sha256: string | null
  /** To a paired phone: what's waiting for its owner, as the Mac's notifications see it (keys, never names). */
  waiting?: string[]
}

/** The phone's own "a review is ready" (Lab.swift LabRefresh): not asked yet, on, not now, refused in Settings. */
export type NotifyState = 'ask' | 'on' | 'off' | 'denied' | 'unsupported'
export interface Notify { state: NotifyState; refresh?: boolean }

export interface LabNative {
  pair(o: { hosts: string[]; port: number; ca: string; secret: string; name: string }): Promise<{ host: string; mac: string }>
  hello(o: { hosts: string[]; port: number; ca: string; timeoutMs?: number }): Promise<{ host: string | null; status: number; paired: boolean; hello?: Hello }>
  show(o: { url: string; hosts: string[]; ca: string; rect: Rect }): Promise<void>
  place(o: { rect: Rect }): Promise<void>
  hide(): Promise<void>
  reload(): Promise<void>
  token(): Promise<{ paired: boolean }>
  forget(): Promise<void>
  scan(): Promise<{ text: string }>
  /** What's on the clipboard: the link copied on the Mac arrives here by Universal Clipboard. */
  paste(): Promise<{ text: string }>
  notifications(): Promise<Notify>
  allowNotifications(): Promise<Notify>
  notNow(): Promise<Notify>
  addListener(event: 'loaded' | 'failed' | 'opened', cb: (d: { url?: string; message?: string; code?: number; tab?: string }) => void): Promise<PluginListenerHandle>
}

/** What the QR code says (octo/phone.py pair_link). */
export interface PairLink { mac: string; hosts: string[]; port: number; ca: string; secret: string }

export function parseLink(text: string): PairLink | null {
  if (!text.startsWith('octo://pair?')) return null
  const q = new URLSearchParams(text.slice('octo://pair?'.length))
  const hosts = (q.get('h') ?? '').split(',').map((h) => h.trim()).filter(Boolean)
  const ca = (q.get('ca') ?? '').toLowerCase()
  const secret = q.get('s') ?? ''
  if (q.get('v') !== '1' || !hosts.length || !/^[0-9a-f]{64}$/.test(ca) || !secret) return null
  return { mac: q.get('n') || 'your Mac', hosts, port: Number(q.get('p')) || 8766, ca, secret }
}

/** Tailscale's addresses: 100.64.0.0/10. */
export function isTailnet(host: string): boolean {
  const m = /^100\.(\d+)\.\d+\.\d+$/.exec(host)
  return !!m && (Number(m[1]) & 0xc0) === 64
}

export interface LabConfig { mac: string; hosts: string[]; port: number; ca: string; pairedAt: number; reachedAt?: number }

export type LabPhase = 'loading' | 'none' | 'pairing' | 'pair_failed' | 'reaching' | 'ready' | 'unreachable' | 'removed'

export interface LabStore { load(): Promise<LabConfig | null>; save(c: LabConfig | null): Promise<void> }

const KEY = 'octo.lab'
/** Can't reach the Mac: look again on its own for a minute (14 Oct: Tailscale came up on the phone after the
 * app's one look, and it stayed on "Couldn't reach your Mac" until it was closed and opened). */
const RETRY_MS = 5000
const RETRIES = 12
export const prefsLabStore: LabStore = {
  async load() {
    try { const { value } = await Preferences.get({ key: KEY }); return value ? JSON.parse(value) as LabConfig : null } catch { return null }
  },
  async save(c) {
    try { if (c) await Preferences.set({ key: KEY, value: JSON.stringify(c) }); else await Preferences.remove({ key: KEY }) } catch { /* next time */ }
  },
}

export class Lab {
  readonly s = reactive({
    phase: 'loading' as LabPhase,
    cfg: null as LabConfig | null,
    host: null as string | null,
    /** Why pairing didn't work, in plain words. */
    failure: '',
    /** The lab's page failed to load (it was reachable a moment ago). */
    lost: false,
    /** Notifications when something's waiting: asked once the lab is reached, never before (just in time). */
    notify: null as Notify | null,
  })

  private retry: ReturnType<typeof setTimeout> | null = null
  private tries = 0

  constructor(private native: LabNative, private store: LabStore = prefsLabStore) {}

  get away() { return !!this.s.host && isTailnet(this.s.host) }
  get url() { return this.s.host && this.s.cfg ? `https://${this.s.host.includes(':') ? `[${this.s.host}]` : this.s.host}:${this.s.cfg.port}/` : '' }
  get hasTailscale() { return !!this.s.cfg?.hosts.some(isTailnet) }

  async load() {
    const cfg = await this.store.load()
    const { paired } = await this.native.token().catch(() => ({ paired: false }))
    if (!cfg || !paired) { this.s.phase = 'none'; return }
    this.s.cfg = cfg
    await this.reach()
  }

  /** Scan the Mac's code in the app. */
  async scan() {
    this.s.failure = ''
    let text: string
    try {
      text = (await this.native.scan()).text
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      if (/cancel/i.test(m)) return
      this.s.failure = /not allowed/i.test(m) ? 'Octo can\'t use the camera. Allow it in Settings, or point the iPhone\'s Camera app at the code.'
        : 'This iPhone can\'t scan codes in the app. Point the iPhone\'s Camera app at the code instead.'
      this.s.phase = this.s.cfg ? this.s.phase : 'none'
      return
    }
    await this.pairWith(text)
  }

  /** The link copied on the Mac (Phone → Copy the link): the same as scanning it, without the camera. */
  async paste() {
    this.s.failure = ''
    const text = (await this.native.paste().catch(() => ({ text: '' }))).text.trim()
    if (!text) {
      this.s.failure = 'Nothing to paste yet. On your Mac: Phone → Show the code → Copy the link. Then paste here.'
      this.s.phase = this.s.cfg ? this.s.phase : 'none'
      return
    }
    await this.pairWith(text)
  }

  /** From a scanned code, or the Camera app opening octo://pair?... */
  async pairWith(text: string) {
    const link = parseLink(text)
    if (!link) { this.s.failure = 'That isn\'t a code from Octo on your Mac.'; this.s.phase = 'pair_failed'; return }
    this.s.phase = 'pairing'
    this.s.failure = ''
    try {
      const r = await this.native.pair({ hosts: link.hosts, port: link.port, ca: link.ca, secret: link.secret, name: 'iPhone' })
      this.s.cfg = { mac: r.mac || link.mac, hosts: link.hosts, port: link.port, ca: link.ca, pairedAt: Date.now() }
      await this.store.save(this.s.cfg)
      await this.reach()
    } catch (e) {
      const m = e instanceof Error ? e.message : String(e)
      this.s.failure = /used|expired/.test(m) ? 'That code was used or ran out. Show a new one on your Mac and scan again.'
        : /unreachable/.test(m) ? 'Couldn\'t reach your Mac. Be on the same Wi-Fi as it (or on Tailscale), check Local Network is on for Octo (Settings → Octo), then scan again.'
        : 'Your Mac didn\'t accept this phone. Show a new code on your Mac and scan again.'
      this.s.phase = 'pair_failed'
    }
  }

  /** Find the Mac: whichever of its addresses answers first. `again`: the app's own retry, so the screen stays put. */
  async reach(again = false) {
    this.stopTrying()
    if (!again) this.tries = 0
    const cfg = this.s.cfg
    if (!cfg) { this.s.phase = 'none'; return }
    if (!again) {
      this.s.phase = this.s.phase === 'ready' ? 'ready' : 'reaching'
      this.s.lost = false
    }
    // Plain copies: this.s is reactive, and a Vue proxy reaches the native side as nothing (8 Oct: no address tried).
    const r = await this.native.hello({ hosts: [...cfg.hosts], port: cfg.port, ca: cfg.ca }).catch(() => ({ host: null, status: 0, paired: true }) as Awaited<ReturnType<LabNative['hello']>>)
    if (r.status === 401) { this.s.phase = 'removed'; this.s.host = null; return }
    if (!r.host) { this.s.phase = 'unreachable'; this.s.host = null; this.keepTrying(); return }
    this.s.lost = false
    this.s.host = r.host
    if (r.hello) {
      // The Mac's addresses change (a new Wi-Fi address, Tailscale switched on): follow what it says now,
      // keeping the one that just answered. A Mac with Tailscale off lists no Tailscale address: keep the one
      // known, or away from home there's nowhere to look (13 Oct, "Can't reach your Mac").
      const keep = r.hello.tailscale?.length ? [] : cfg.hosts.filter(isTailnet)
      const hosts = [...new Set([...r.hello.hosts, r.host, ...keep])]
      this.s.cfg = { ...cfg, mac: r.hello.mac || cfg.mac, hosts, reachedAt: Date.now() }
    } else {
      this.s.cfg = { ...cfg, reachedAt: Date.now() }
    }
    await this.store.save(this.s.cfg)
    this.s.phase = 'ready'
    this.s.notify = await this.native.notifications().catch(() => ({ state: 'unsupported' as const }))
  }

  private keepTrying() {
    if (this.retry || this.tries >= RETRIES) return
    this.retry = setTimeout(() => { this.retry = null; this.tries++; void this.reach(true) }, RETRY_MS)
  }
  private stopTrying() {
    if (this.retry) clearTimeout(this.retry)
    this.retry = null
  }

  /** "Tell me when a review is ready": iOS asks for permission now, not before. */
  async allowNotifications() { this.s.notify = await this.native.allowNotifications().catch(() => ({ state: 'unsupported' as const })) }
  async notNow() { this.s.notify = await this.native.notNow().catch(() => ({ state: 'off' as const })) }
  /** A notification was tapped: the app opens on the Lab tab. */
  onOpened(cb: () => void) { return this.native.addListener('opened', () => cb()) }

  // ---- the native view over the tab ----

  show(rect: Rect) {
    const cfg = this.s.cfg
    return cfg ? this.native.show({ url: this.url, hosts: [...cfg.hosts], ca: cfg.ca, rect: { ...rect } }) : Promise.resolve()
  }
  place(rect: Rect) { return this.native.place({ rect }) }
  hide() { return this.native.hide() }
  reload() { return this.native.reload() }
  /** The native view tells us when a page fails to load. */
  listen() { return this.native.addListener('failed', () => this.failed()) }

  /** The page couldn't load: the Mac went to sleep or the network changed. */
  failed() {
    this.s.lost = true
    this.s.phase = 'unreachable'
    void this.native.hide()
    this.tries = 0
    this.keepTrying()
  }

  async forget() {
    this.stopTrying()
    await this.native.forget().catch(() => {})
    await this.store.save(null)
    this.s.cfg = null
    this.s.host = null
    this.s.phase = 'none'
  }
}

/** In a browser (?demo): a made-up Mac, so the tab's states can be seen. */
export class FakeLabNative implements LabNative {
  paired = false
  reachable = true
  private wait = (ms = 600) => new Promise((r) => setTimeout(r, ms))
  async pair() { await this.wait(900); this.paired = true; return { host: '192.168.1.20', mac: 'Sam\'s MacBook (demo)' } }
  async hello() {
    await this.wait()
    if (!this.paired) return { host: null, status: 401, paired: false }
    if (!this.reachable) return { host: null, status: 0, paired: true }
    return { host: '192.168.1.20', status: 200, paired: true, hello: { mac: 'Sam\'s MacBook (demo)', hosts: ['sams-mac.local', '192.168.1.20', '100.101.102.103'], home: ['sams-mac.local', '192.168.1.20'], tailscale: ['100.101.102.103'], port: 8766, device: 'iPhone', ca_sha256: 'a'.repeat(64) } }
  }
  async show() {}
  async place() {}
  async hide() {}
  async reload() {}
  async token() { return { paired: this.paired } }
  async forget() { this.paired = false }
  async scan() { await this.wait(1200); return { text: `octo://pair?v=1&n=Sam%27s%20MacBook%20(demo)&h=sams-mac.local,192.168.1.20,100.101.102.103&p=8766&ca=${'a'.repeat(64)}&s=demo-secret` } }
  async paste() { return this.scan() }
  notify: Notify = { state: 'ask', refresh: true }
  async notifications() { return this.notify }
  async allowNotifications() { await this.wait(400); this.notify = { state: 'on', refresh: true }; return this.notify }
  async notNow() { this.notify = { state: 'off', refresh: true }; return this.notify }
  async addListener() { return { remove: async () => {} } }
}

export const nativeLab = (): LabNative => (Capacitor.isNativePlatform() ? registerPlugin<LabNative>('Lab') : new FakeLabNative())
