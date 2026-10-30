// The phone's connection to its recorder (docs/phone-plan.md §3): find it once, then remember it and keep
// reconnecting by itself (on launch, when the app comes back, after a drop, an update or a restart). A known
// recorder never goes back to the find screen; only "Forget this recorder" does that.
//
// Every update ends in a restart, and before 8 Oct the app dropped to "Find" on any disconnect: that, not
// the Bluetooth pairing (kept in the recorder's flash), is why it looked like it had to pair again.
import { reactive } from 'vue'
import { mergeStatus, trouble } from './recorder'
import type { Found, Link, Status, Trouble } from './recorder'

export interface Remembered {
  id: string
  name: string
  fw?: string
  boot?: number
  /** When the app last had a status from it (ms), and that status: shown dimmed while reconnecting. */
  seen?: number
  status?: Status
}

export interface Store {
  load(): Promise<Remembered | null>
  save(r: Remembered | null): Promise<void>
}

export type Phase =
  | 'loading'
  | 'hello'          // nothing remembered: the first run
  | 'searching'
  | 'cant_find'      // a search that found nothing
  | 'found'          // waiting for the person to choose (and look at the recorder for the code)
  | 'connecting'     // the first time, to the one they chose
  | 'code'           // the first read is waiting: iOS is showing its code box
  | 'pair_failed'
  | 'connected'
  | 'reconnecting'   // a known recorder that isn't connected right now: trying, by itself
  | 'bluetooth_off'
  | 'not_allowed'
  | 'unsupported'
  | 'forgotten'      // the recorder no longer knows this phone

/** Why a known recorder isn't connected: an update or restart it announced, a drop, or the app just opened. */
export type Why = 'open' | 'update' | 'lost' | 'back'

export type EventKind = 'connected_first' | 'reconnected' | 'updated' | 'rolled_back' | 'started' | 'stopped'
export interface ConnEvent { kind: EventKind; fw?: string; from?: string; n: number }

export interface Timing {
  /** One connect attempt. */
  attemptMs: number
  /** The first read after connecting: the person may be typing the code. */
  firstReadMs: number
  /** No answer by then: probably the code box ("code" phase). */
  codeAfterMs: number
  /** A search gives up after this. */
  searchMs: number
  /** After the first recorder turns up, listen this much longer for a second. */
  moreMs: number
  /** Waits between attempts (each attempt itself waits up to attemptMs for the recorder to appear); the last repeats. */
  backoff: number[]
}

export const TIMING: Timing = {
  attemptMs: 8000, firstReadMs: 60000, codeAfterMs: 600, searchMs: 30000, moreMs: 1500,
  backoff: [0, 500, 1000, 2000, 4000, 8000],
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export class Connection {
  readonly s = reactive({
    phase: 'loading' as Phase,
    found: [] as Found[],
    recorder: null as Remembered | null,
    /** The last status: live while connected, the last known one otherwise. */
    status: null as Status | null,
    nets: [] as string[],
    why: 'open' as Why,
    /** When reconnecting began (ms). */
    since: 0,
    failure: null as Trouble | null,
    event: null as ConnEvent | null,
    /** Searching from a known recorder ("Find a different recorder"). */
    replacing: false,
  })

  private gen = 0       // bumped to cancel a running loop or search
  private events = 0
  /** A new version waiting for the Mac's confirmation: "updated" is said once it's confirmed. */
  private confirming: { fw: string; from?: string } | null = null
  private active = true // the app is in the foreground
  private t: Timing

  constructor(private link: Link, private store: Store, timing: Partial<Timing> = {}) {
    this.t = { ...TIMING, ...timing }
  }

  // ---- start, search, choose ----

  async start() {
    const r = await this.store.load()
    this.s.recorder = r
    this.s.status = r?.status ?? null
    if (!r) { this.s.phase = 'hello'; return }
    if (!(await this.ready())) return
    if (!(await this.link.known(r.id).catch(() => true))) {
      // iOS no longer knows it (the phone was reset, or it was forgotten in Settings): find it again.
      await this.forgetLocally()
      return
    }
    this.reconnect('open')
  }

  /** Readies Bluetooth; false (and the phase says why) when it can't be used. */
  private async ready(): Promise<boolean> {
    try {
      await this.link.init()
    } catch (e) {
      const t = trouble(e)
      this.s.phase = t === 'not_allowed' ? 'not_allowed' : t === 'off' ? 'bluetooth_off' : 'unsupported'
      return false
    }
    await this.link.onEnabled((on) => this.bluetooth(on)).catch(() => {})
    if (!(await this.link.enabled().catch(() => true))) { this.s.phase = 'bluetooth_off'; return false }
    return true
  }

  private bluetooth(on: boolean) {
    if (!on) {
      this.gen++
      this.s.phase = 'bluetooth_off'
    } else if (this.s.phase === 'bluetooth_off') {
      if (this.s.recorder) this.reconnect('back')
      else this.s.phase = 'hello'
    }
  }

  async search() {
    const gen = ++this.gen
    this.s.found = []
    this.s.failure = null
    if (!(await this.ready())) return
    if (gen !== this.gen) return
    this.s.phase = 'searching'
    let first = 0
    try {
      await this.link.scan((f) => {
        if (gen !== this.gen) return
        const i = this.s.found.findIndex((x) => x.id === f.id)
        if (i >= 0) this.s.found[i] = f
        else this.s.found.push(f)
        this.s.found.sort((a, b) => (b.rssi ?? -127) - (a.rssi ?? -127))
        if (!first) first = Date.now()
      })
    } catch (e) {
      if (gen === this.gen) this.fail(e)
      return
    }
    const until = Date.now() + this.t.searchMs
    while (gen === this.gen && Date.now() < until && !(first && Date.now() - first >= this.t.moreMs)) await sleep(50)
    if (gen !== this.gen) return
    await this.link.stopScan()
    this.s.phase = this.s.found.length ? 'found' : 'cant_find'
  }

  async cancelSearch() {
    this.gen++
    await this.link.stopScan()
    if (this.s.recorder && this.s.replacing) { this.s.replacing = false; this.reconnect('back') }
    else this.s.phase = 'hello'
  }

  /** Connect to a recorder found nearby. The first read brings up iOS's code box the first time. */
  async choose(f: Found) {
    const gen = ++this.gen
    await this.link.stopScan()
    if (this.s.replacing) await this.link.disconnect()
    this.s.phase = 'connecting'
    this.s.failure = null
    try {
      await this.link.connect(f.id, () => this.dropped(f.id), this.t.attemptMs + 2000)
      const codeTimer = setTimeout(() => { if (gen === this.gen && this.s.phase === 'connecting') this.s.phase = 'code' }, this.t.codeAfterMs)
      let st: Status
      try { st = await this.link.status(this.t.firstReadMs) } finally { clearTimeout(codeTimer) }
      if (gen !== this.gen) return
      await this.remember({ id: f.id, name: st.name ?? f.name, fw: st.fw, boot: st.boot }, st)
      this.s.replacing = false
      await this.live(gen, st)
      this.emit('connected_first')
    } catch (e) {
      if (gen !== this.gen) return
      await this.link.disconnect()
      const t = trouble(e)
      if (t === 'off' || t === 'not_allowed') return this.fail(e)
      this.s.failure = t
      this.s.phase = t === 'removed' ? 'forgotten' : 'pair_failed'
    }
  }

  // ---- staying connected ----

  /** Keep trying a known recorder until it answers, the app goes to the background, or Bluetooth goes off. */
  reconnect(why: Why) {
    const r = this.s.recorder
    if (!r) return
    const gen = ++this.gen
    this.s.why = why
    this.s.since = Date.now()
    this.s.phase = 'reconnecting'
    void this.loop(gen, r)
  }

  private async loop(gen: number, r: Remembered) {
    for (let i = 0; gen === this.gen && this.active; i++) {
      await sleep(this.t.backoff[Math.min(i, this.t.backoff.length - 1)])
      if (gen !== this.gen || !this.active) return
      try {
        await this.link.connect(r.id, () => this.dropped(r.id), this.t.attemptMs)
        if (gen !== this.gen) { await this.link.disconnect(); return }
        const codeTimer = setTimeout(() => { if (gen === this.gen) this.s.phase = 'code' }, 3000)
        let st: Status
        try { st = await this.link.status(this.t.firstReadMs) } finally { clearTimeout(codeTimer) }
        if (gen !== this.gen) return
        const before = { fw: r.fw, boot: r.boot }
        await this.remember({ ...r, name: st.name ?? r.name, fw: st.fw, boot: st.boot }, st)
        await this.live(gen, st)
        if (st.rolled_back && before.boot !== st.boot) this.emit('rolled_back', st.fw)
        else if (before.fw && st.fw !== before.fw) {
          if (st.pending) this.confirming = { fw: st.fw, from: before.fw }
          else this.emit('updated', st.fw, before.fw)
        } else if (this.s.why !== 'open') this.emit('reconnected')
        return
      } catch (e) {
        if (gen !== this.gen) return
        const t = trouble(e)
        if (t === 'removed') { await this.link.disconnect(); this.s.phase = 'forgotten'; return }
        if (t === 'off') { this.s.phase = 'bluetooth_off'; return }
        if (t === 'not_allowed') { this.s.phase = 'not_allowed'; return }
        await this.link.disconnect()
        if (this.s.phase === 'code') this.s.phase = 'reconnecting'
      }
    }
  }

  private async live(gen: number, st: Status) {
    this.s.status = st
    this.s.nets = await this.link.networks().catch(() => this.s.nets)
    await this.link.onStatus((live) => {
      if (gen !== this.gen) return
      this.s.status = mergeStatus(this.s.status, live)
      if (this.confirming && this.s.status && !this.s.status.pending) {
        this.emit('updated', this.confirming.fw, this.confirming.from)
        this.confirming = null
      }
      if (this.s.recorder && this.s.status) {
        this.s.recorder.status = this.s.status
        this.s.recorder.seen = Date.now()
      }
    }).catch(() => {})
    if (gen === this.gen) this.s.phase = 'connected'
  }

  private dropped(id: string) {
    if (this.s.recorder?.id !== id || this.s.phase !== 'connected') return
    const st = this.s.status
    const announced = !!st && ((st.upd ?? -1) >= 0 || !!st.restart)
    void this.saveSeen()
    this.reconnect(announced ? 'update' : 'lost')
  }

  /** The app came to the front: check the link still answers, else reconnect. */
  async foreground() {
    this.active = true
    const p = this.s.phase
    if (p === 'connected') {
      const gen = this.gen
      try {
        const st = await this.link.status(3000)
        if (gen === this.gen) this.s.status = st
      } catch {
        if (gen === this.gen) { await this.link.disconnect(); this.reconnect('back') }
      }
    } else if (p === 'reconnecting' || p === 'code') {
      this.reconnect(this.s.why)
    }
  }

  /** The app went to the back: stop trying (iOS suspends it anyway); a live link is left alone. */
  background() {
    this.active = false
    if (this.s.phase !== 'connected') this.gen++
    void this.link.stopScan()
    void this.saveSeen()
  }

  // ---- what the person does ----

  async setRecording(on: boolean) {
    await this.link.setRecording(on)
    this.s.status = mergeStatus(this.s.status, { want_on: on })
    try { this.s.status = await this.link.status() } catch { /* the notification follows in 2 s */ }
    this.emit(on ? 'started' : 'stopped')
  }

  async setFiller(on: boolean) {
    await this.link.setFiller(on)
    this.s.status = mergeStatus(this.s.status, { filler_on: on })
    try { this.s.status = await this.link.status() } catch { /* shown as set; the next read says */ }
  }

  async addNetwork(ssid: string, password: string) { this.s.nets = await this.link.addNetwork(ssid, password) }
  async removeNetwork(ssid: string) { this.s.nets = await this.link.removeNetwork(ssid) }

  /** Stop using this recorder: the app forgets it (iOS keeps its own pairing until it's forgotten in Settings). */
  async forget() {
    this.gen++
    await this.link.disconnect()
    await this.forgetLocally()
  }

  /** "Find a different recorder": search while the known one stays known until another is chosen. */
  async findAnother() {
    this.s.replacing = true
    await this.search()
  }

  /** After "forgotten" or a failed first pairing: start over. */
  async retry() {
    if (this.s.phase === 'forgotten') { await this.forget(); return this.search() }
    if (this.s.recorder && !this.s.replacing) return this.reconnect('back')
    return this.search()
  }

  async openSettings() { await this.link.openSettings().catch(() => {}) }

  // ---- inside ----

  private async forgetLocally() {
    this.s.recorder = null
    this.s.status = null
    this.s.nets = []
    this.s.replacing = false
    this.s.phase = 'hello'
    await this.store.save(null)
  }

  private async remember(r: Remembered, st: Status) {
    const now = Date.now()
    this.s.recorder = { ...r, seen: now, status: st }
    await this.store.save(this.s.recorder)
  }

  private async saveSeen() {
    if (this.s.recorder) await this.store.save({ ...this.s.recorder }).catch(() => {})
  }

  private fail(e: unknown) {
    const t = trouble(e)
    this.s.phase = t === 'not_allowed' ? 'not_allowed' : t === 'off' ? 'bluetooth_off' : t === 'unsupported' ? 'unsupported' : 'cant_find'
    this.s.failure = t
  }

  private emit(kind: EventKind, fw?: string, from?: string) {
    this.s.event = { kind, fw, from, n: ++this.events }
  }
}

/** Kept in memory: the tests, and the browser demo. */
export class MemoryStore implements Store {
  constructor(public r: Remembered | null = null) {}
  async load() { return this.r ? { ...this.r } : null }
  async save(r: Remembered | null) { this.r = r ? JSON.parse(JSON.stringify(r)) : null }
}
