// A simulated recorder for the browser (?demo) and the tests. It keeps the firmware's rules (octo_settings.h,
// docs/recorder-ble.md) and can act out what a real one does to the app: go out of range, restart, take an
// update (with progress, a restart, "checking with the Mac", or going back), ask for a code the first time.
// Never used in the phone build unless the URL asks for it.
import { addCommand, networkProblem, removeCommand } from './recorder'
import type { DropHandler, Found, Link, Status } from './recorder'

const MAX_NETS = 5
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

export interface FakeTimes {
  /** Every call's delay. */
  call: number
  /** How long the person takes to type the code the first time. */
  code: number
  /** Restart after an update or a reset, until it's back. */
  restart: number
  /** Between update progress steps. */
  updateStep: number
}

export class FakeLink implements Link {
  nets: { ssid: string; pass: string }[] = [{ ssid: 'Home', pass: 'home-password' }]
  wantOn = true
  /** rec-24's filler cue: undefined on a recorder without it (the fake's default, as before) */
  fillerOn: boolean | undefined = undefined
  fw = 'rec-15'
  boot = 45
  name = 'Octo 1A2B'
  version = 2
  state: Status['state'] | null = null  // forced state ("needs you"), else from wantOn
  recStart = Date.now() - 12 * 60 * 1000
  upd = -1
  restarting = false
  pending = false
  rolledBack = false
  /** In range and advertising. */
  nearby = true
  /** The phone and the recorder share a pairing (the first read asks for the code until they do). */
  bonded = false
  /** The recorder forgot this phone (iOS then says "Peer removed pairing information"). */
  forgotPhone = false
  btOn = true
  connected = false
  /** What the person does with the code box: type it, or cancel. */
  codeAnswer: 'type' | 'cancel' | 'wrong' = 'type'
  times: FakeTimes

  private listeners: ((s: Partial<Status>) => void)[] = []
  private enabledCbs: ((on: boolean) => void)[] = []
  private drop: DropHandler | null = null
  private timer: ReturnType<typeof setInterval> | undefined
  private scanning: ((f: Found) => void) | null = null

  constructor(times: Partial<FakeTimes> | number = {}) {
    const t = typeof times === 'number' ? { call: times, code: times, restart: times, updateStep: times } : times
    this.times = { call: 300, code: 2500, restart: 6000, updateStep: 250, ...t }
  }

  private wait() { return wait(this.times.call) }

  current(): Status {
    const state = this.state ?? (this.wantOn ? 'recording' : 'off')
    const s: Status = {
      fw: this.fw, state, want_on: this.wantOn, recording: state === 'recording',
      card_free_mb: 27650, card_total_mb: 29600, wifi: !!this.nets.length, ssid: this.nets[0]?.ssid ?? '', rssi: -63,
      networks: this.nets.length, clock_set: true, battery_mv: -1,
    }
    if (this.version >= 2) {
      Object.assign(s, {
        v: 2, name: this.name, boot: this.boot, rec_s: state === 'recording' ? Math.floor((Date.now() - this.recStart) / 1000) : 0,
        upd: this.upd, restart: this.restarting, pending: this.pending, rolled_back: this.rolledBack,
      })
    }
    if (this.fillerOn !== undefined) Object.assign(s, { filler_model: true, filler_on: this.fillerOn, fillers: this.fillerOn ? 3 : 0 })
    return s
  }

  private tell() { const s = this.current(); this.listeners.forEach((cb) => cb(s)) }

  async init() { await this.wait() }
  async enabled() { return this.btOn }
  async onEnabled(cb: (on: boolean) => void) { this.enabledCbs.push(cb) }
  async openSettings() {}

  async scan(cb: (f: Found) => void) {
    if (!this.btOn) throw new Error('Bluetooth not enabled')
    this.scanning = cb
    if (this.nearby) setTimeout(() => this.scanning?.({ id: 'fake', name: this.name, rssi: -58 }), this.times.call * 4)
  }
  async stopScan() { this.scanning = null }

  async known(id: string) { return id === 'fake' }

  async connect(_id: string, onDrop: DropHandler, timeoutMs: number) {
    if (!this.btOn) throw new Error('Bluetooth not enabled')
    if (!this.nearby) { await wait(Math.min(timeoutMs, this.times.call * 3)); throw new Error('Connection timeout.') }
    await this.wait()
    this.connected = true
    this.drop = onDrop
    clearInterval(this.timer)
    this.timer = setInterval(() => this.tell(), 2000)
  }

  async status(timeoutMs = 5000): Promise<Status> {
    this.need()
    if (this.forgotPhone) { await this.wait(); throw new Error('Peer removed pairing information') }
    if (!this.bonded) {
      // The phone's code box: the person types it (or doesn't).
      if (this.times.code > timeoutMs) { await wait(timeoutMs); throw new Error('Read timeout.') }
      await wait(this.times.code)
      if (this.codeAnswer === 'cancel') throw new Error('Authentication is insufficient.')
      if (this.codeAnswer === 'wrong') throw new Error('Authentication is insufficient.')
      this.bonded = true
    }
    await this.wait()
    return this.current()
  }

  async onStatus(cb: (s: Partial<Status>) => void) { this.listeners.push(cb) }

  async setRecording(on: boolean) {
    this.need()
    await this.wait()
    if (on && !this.wantOn) this.recStart = Date.now()
    this.wantOn = on
    this.tell()
  }

  async setFiller(on: boolean) {
    this.need()
    await this.wait()
    if (this.fillerOn !== undefined) this.fillerOn = on  // a firmware without the model ignores it
    this.tell()
  }

  async networks() { this.need(); await this.wait(); return this.nets.map((n) => n.ssid) }

  // The firmware's own rules, from the written command: a bad one changes nothing.
  write(cmd: string) {
    const [op, ssid = '', ...rest] = cmd.split('\n')
    const pass = rest.join('\n')
    if (op === 'add' && !networkProblem(ssid, pass)) {
      this.nets = [{ ssid, pass }, ...this.nets.filter((n) => n.ssid !== ssid)].slice(0, MAX_NETS)
    } else if (op === 'del') {
      this.nets = this.nets.filter((n) => n.ssid !== ssid)
    }
  }

  async addNetwork(ssid: string, password: string) { this.need(); await this.wait(); this.write(addCommand(ssid, password)); return this.networks() }

  async removeNetwork(ssid: string) { this.need(); await this.wait(); this.write(removeCommand(ssid)); return this.networks() }

  async disconnect() { this.cut(false) }

  private need() { if (!this.connected) throw new Error('Device not connected') }

  /** The link ends: `notify` when the recorder ended it (the app's drop handler runs). */
  private cut(notify: boolean) {
    clearInterval(this.timer)
    this.listeners = []
    const was = this.connected
    this.connected = false
    const d = this.drop
    this.drop = null
    if (notify && was) d?.()
  }

  // ---- what a real recorder does (demo buttons and tests) ----

  /** Walk out of range (or back). */
  setNearby(on: boolean) {
    this.nearby = on
    if (!on) this.cut(true)
    else if (this.scanning) this.scanning({ id: 'fake', name: this.name, rssi: -70 })
  }

  setBluetooth(on: boolean) {
    this.btOn = on
    if (!on) this.cut(true)
    this.enabledCbs.forEach((cb) => cb(on))
  }

  /** The reset button, or a power cycle. */
  async restart() {
    this.cut(true)
    this.nearby = false
    await wait(this.times.restart)
    this.boot++
    this.upd = -1
    this.restarting = false
    this.nearby = true
  }

  /** An update from the Mac, as the recorder lives it: pieces arrive, it restarts, the Mac confirms it (or it
   *  goes back). The app sees the percent only from rec-15 (status v2). */
  async update(to = 'rec-16', outcome: 'kept' | 'rolled_back' = 'kept') {
    for (let p = 0; p <= 100; p += 7) {
      this.upd = Math.min(p, 100)
      this.tell()
      await wait(this.times.updateStep)
    }
    this.upd = 100
    this.restarting = true
    this.tell()
    await wait(this.times.updateStep)
    const from = this.fw
    await this.restart()
    if (outcome === 'kept') {
      this.fw = to
      this.pending = true
      setTimeout(() => { this.pending = false; this.tell() }, this.times.restart / 2)
    } else {
      this.fw = from
      this.rolledBack = true
    }
  }
}
