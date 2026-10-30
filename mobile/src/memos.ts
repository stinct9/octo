// Memos (docs/backlog.md B8, B9): record on this phone, or Share → Octo from Voice Memos. Either way the file waits
// in an outbox on the phone (ios/App/App/Memos.swift) and goes to the paired Mac as soon as the lab answers.
import { reactive } from 'vue'
import { Capacitor, registerPlugin } from '@capacitor/core'
import type { PluginListenerHandle } from '@capacitor/core'
import { Preferences } from '@capacitor/preferences'
import type { Lab } from './lab'

export interface MemoItem { id: string; name: string; size: number; modified: number }

export interface MemosNative {
  pending(): Promise<{ items: MemoItem[] }>
  /** A file iOS handed to Octo: into the outbox. */
  take(o: { url: string }): Promise<MemoItem>
  /** 200 (the Mac has it) and 409 (it had it already) take it off the phone; 0: the Mac didn't answer. */
  send(o: { id: string; host: string; port: number; ca: string }): Promise<{ status: number; message?: string }>
  remove(o: { id: string }): Promise<void>
  /** With `cue` (the owner's filler list), the live filler cue too (B21); `haptic` taps for each one. */
  start(o?: { cue?: string[]; haptic?: boolean }): Promise<{ startedAt: number }>
  stop(): Promise<MemoItem>
  /** `cueAvailable`: iOS 26 or later, on a phone that recognises speech on itself. */
  recording(): Promise<{ on: boolean; startedAt?: number; cueAvailable?: boolean }>
  /** B21: the owner's filler list from the Mac at `host`. 0: the Mac didn't answer. */
  fillers(o: { host: string; port: number; ca: string }): Promise<{ status: number; phrases: string[] }>
  /** Each filler the cue heard (late: only Apple's settled pass heard it), and why a cue didn't start. */
  addListener(e: 'filler', cb: (d: { phrase: string; late: boolean }) => void): Promise<PluginListenerHandle>
  addListener(e: 'fillerFailed', cb: (d: { text: string }) => void): Promise<PluginListenerHandle>
  /** B16: "this mattered", to the Mac at `host`, whose clock places it. 0: the Mac didn't answer. */
  /** With `undo`, takes back the mark of that id instead (the Mac allows it for a minute). */
  mark(o: { host: string; port: number; ca: string; undo?: string }): Promise<{ status: number; id?: string }>
}

/** A file URL iOS opened Octo with (Share → Octo), as opposed to octo://pair. */
export const isFile = (url: string | undefined): url is string => !!url && url.startsWith('file://')

/** The live filler cue's settings on this phone (B21): off until the owner turns it on, and the list kept from the Mac. */
export interface CueSettings { on: boolean; haptic: boolean; phrases: string[] }
export interface CueStore { load(): Promise<CueSettings | null>; save(c: CueSettings): Promise<void> }
const CUE_KEY = 'octo.fillerCue'
export const prefsCue: CueStore = {
  async load() {
    try { const { value } = await Preferences.get({ key: CUE_KEY }); return value ? JSON.parse(value) as CueSettings : null } catch { return null }
  },
  async save(c) { try { await Preferences.set({ key: CUE_KEY, value: JSON.stringify(c) }) } catch { /* next time */ } },
}

export class Memos {
  readonly s = reactive({
    items: [] as MemoItem[],
    recording: false,
    startedAt: 0,
    sending: false,
    /** Sent to the Mac since the app opened. */
    sent: 0,
    /** Why a file wasn't taken, by id. */
    errors: {} as Record<string, string>,
    /** Why something couldn't be done just now, in plain words. */
    failure: '',
    /** B16: when the Mac last took a mark from this phone, and why the last one didn't reach it. */
    markedAt: 0,
    markError: '',
    /** B21: the live filler cue. `flash` is the filler just heard, for 1.5 s; `count` this memo's. */
    cue: { available: false, on: false, haptic: false, phrases: [] as string[], flash: '', count: 0, failure: '' },
  })

  constructor(private native: MemosNative, private lab: Lab, private cueStore: CueStore = prefsCue) {}

  async load() {
    await this.refresh()
    const r = await this.native.recording().catch(() => ({ on: false, startedAt: undefined, cueAvailable: false }))
    this.s.recording = r.on
    this.s.startedAt = r.startedAt ?? 0
    const c = this.s.cue
    c.available = !!r.cueAvailable
    Object.assign(c, await this.cueStore.load() ?? {})
    void this.native.addListener('filler', ({ phrase, late }) => this.heard(phrase, late)).catch(() => {})
    void this.native.addListener('fillerFailed', ({ text }) => { c.failure = text }).catch(() => {})
  }

  private flashTimer: ReturnType<typeof setTimeout> | undefined
  /** The filler big for 1.5 s, as the recorder's screen (B20); a late one only counts. */
  heard(phrase: string, late = false) {
    const c = this.s.cue
    c.count++
    if (late) return
    c.flash = phrase
    clearTimeout(this.flashTimer)
    this.flashTimer = setTimeout(() => { c.flash = '' }, 1500)
  }

  /** The owner's list from the Mac, kept for recording away from home. */
  async fetchPhrases() {
    const { host, cfg, phase } = this.lab.s
    if (phase !== 'ready' || !host || !cfg) return
    const r = await this.native.fillers({ host, port: cfg.port, ca: cfg.ca }).catch(() => ({ status: 0, phrases: [] }))
    if (r.status !== 200) return
    this.s.cue.phrases = r.phrases
    await this.saveCue()
  }

  async setCue(on: boolean, haptic = this.s.cue.haptic) {
    Object.assign(this.s.cue, { on, haptic, failure: '' })
    await this.saveCue()
    if (on && !this.s.cue.phrases.length) await this.fetchPhrases()
  }

  private saveCue() {
    const { on, haptic, phrases } = this.s.cue
    return this.cueStore.save({ on, haptic, phrases: [...phrases] })
  }

  private async refresh() {
    this.s.items = (await this.native.pending().catch(() => ({ items: [] }))).items
  }

  /** Share → Octo from Voice Memos (or Files). */
  async take(url: string) {
    this.s.failure = ''
    try {
      await this.native.take({ url })
    } catch (e) {
      this.s.failure = /not audio/.test(String(e)) ? 'That isn\'t an audio file Octo can send to your Mac.' : 'Octo couldn\'t keep that file. Share it again.'
    }
    await this.refresh()
    await this.send()
  }

  async start() {
    this.s.failure = ''
    const c = this.s.cue
    Object.assign(c, { count: 0, flash: '', failure: '' })
    const cue = c.on && c.available && c.phrases.length ? { cue: [...c.phrases], haptic: c.haptic } : undefined
    try {
      const r = await this.native.start(cue)
      this.s.recording = true
      this.s.startedAt = r.startedAt
    } catch (e) {
      this.s.failure = /not allowed/.test(String(e)) ? 'Octo can\'t use the microphone. Allow it in Settings → Octo.' : 'The recording didn\'t start. Try again.'
    }
  }

  async stop() {
    try { await this.native.stop() } catch { /* nothing was recording */ }
    this.s.recording = false
    this.s.startedAt = 0
    await this.refresh()
    await this.send()
  }

  async remove(id: string) {
    await this.native.remove({ id }).catch(() => {})
    delete this.s.errors[id]
    await this.refresh()
  }

  /** B16: mark this moment. The Mac's clock places it, so it needs the Mac now: nothing is kept to send later. */
  async mark(): Promise<boolean> {
    this.s.markError = ''
    const { host, cfg, phase } = this.lab.s
    const r = phase === 'ready' && host && cfg ? await this.native.mark({ host, port: cfg.port, ca: cfg.ca }).catch(() => ({ status: 0, id: undefined })) : { status: 0, id: undefined }
    if (r.status === 200) {
      this.s.markedAt = Date.now()
      this.lastMark = r.id ?? ''
      return true
    }
    if (r.status === 0 || r.status === 401) void this.lab.reach()
    this.s.markError = r.status === 0 ? 'Your Mac didn\'t answer, so this moment wasn\'t marked.' : `Your Mac said no (${r.status}). Not marked.`
    return false
  }

  private lastMark = ''
  /** Undo the mark just made (Jay, 22 Oct). False when the Mac didn't take it back. */
  async unmark(): Promise<boolean> {
    const { host, cfg, phase } = this.lab.s
    if (!this.lastMark || phase !== 'ready' || !host || !cfg) return false
    const r = await this.native.mark({ host, port: cfg.port, ca: cfg.ca, undo: this.lastMark }).catch(() => ({ status: 0 }))
    if (r.status !== 200) return false
    this.lastMark = ''
    this.s.markedAt = 0
    return true
  }

  /** Everything waiting, one at a time, to the address that answered last. Stops when the Mac stops answering. */
  async send() {
    const { host, cfg, phase } = this.lab.s
    if (this.s.sending || phase !== 'ready' || !host || !cfg || !this.s.items.length) return
    this.s.sending = true
    try {
      for (const item of [...this.s.items]) {
        const r = await this.native.send({ id: item.id, host, port: cfg.port, ca: cfg.ca }).catch(() => ({ status: 0, message: '' }))
        if (r.status === 200 || r.status === 409) {
          this.s.sent++
          delete this.s.errors[item.id]
          continue
        }
        if (r.status === 0 || r.status === 401) {
          void this.lab.reach()  // asleep, or this phone was unpaired: the Lab tab says which
          break
        }
        this.s.errors[item.id] = r.status === 415 ? 'Your Mac can\'t read this file.'
          : r.status === 413 ? 'Over 1 GB: too big to send.'
          : `Your Mac said no (${r.status}${r.message ? `: ${r.message}` : ''}).`
      }
    } finally {
      this.s.sending = false
      await this.refresh()
    }
  }
}

/** In a browser (?demo), and for the tests: an outbox in memory. */
export class FakeMemosNative implements MemosNative {
  items: MemoItem[] = []
  on = false
  startedAt = 0
  answer = 200
  private n = 0
  private add(name: string): MemoItem {
    const it = { id: `${++this.n}-${name}`, name, size: 1_200_000, modified: Date.now() }
    this.items.push(it)
    return it
  }
  async pending() { return { items: [...this.items] } }
  async take({ url }: { url: string }) {
    const name = decodeURIComponent(url.split('/').pop() ?? '')
    if (!/\.(m4a|mp3|wav|aac|mp4|caf|aif|aiff|flac|ogg|opus|3gp)$/i.test(name)) throw new Error('not audio')
    return this.add(name)
  }
  async send({ id }: { id: string }) {
    if (this.answer === 200 || this.answer === 409) this.items = this.items.filter((x) => x.id !== id)
    return { status: this.answer }
  }
  async remove({ id }: { id: string }) { this.items = this.items.filter((x) => x.id !== id) }
  cue: { cue?: string[]; haptic?: boolean } | undefined
  async start(o?: { cue?: string[]; haptic?: boolean }) { this.on = true; this.startedAt = Date.now(); this.cue = o; return { startedAt: this.startedAt } }
  async stop() {
    if (!this.on) throw new Error('not recording')
    this.on = false
    return this.add('Octo recording.m4a')
  }
  cueAvailable = true
  async recording() { return { on: this.on, startedAt: this.on ? this.startedAt : undefined, cueAvailable: this.cueAvailable } }
  phrases = ['um', 'uh', 'you know', 'kind of']
  async fillers() { return { status: this.answer, phrases: this.answer === 200 ? [...this.phrases] : [] } }
  private listeners: Record<string, (d: never) => void> = {}
  async addListener(e: string, cb: (d: never) => void) {
    this.listeners[e] = cb
    return { remove: async () => { delete this.listeners[e] } }
  }
  /** What the native cue would say: a filler heard. */
  hear(phrase: string, late = false) { this.listeners.filler?.({ phrase, late } as never) }
  marks = 0
  async mark(o: { undo?: string }) {
    if (this.answer === 200) this.marks += o.undo ? -1 : 1
    return { status: this.answer, id: o.undo ? undefined : `mark_${this.marks}` }
  }
}

export const nativeMemos = (): MemosNative => (Capacitor.isNativePlatform() ? registerPlugin<MemosNative>('Memos') : new FakeMemosNative())
