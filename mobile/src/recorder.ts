// What the app and the recorder agree on over Bluetooth (docs/recorder-ble.md), and the link the screens
// use: the real one (ble.ts) or a simulated recorder (fake.ts).

export const SERVICE = '6f63746f-0001-4c61-8000-6f63746f7265'
export const STATUS = '6f63746f-0002-4c61-8000-6f63746f7265'
export const CONTROL = '6f63746f-0003-4c61-8000-6f63746f7265'
export const NETWORKS = '6f63746f-0004-4c61-8000-6f63746f7265'

/** The newest status version this app understands (rec-15 sends 2; rec-12 to rec-14 send none). */
export const STATUS_VERSION = 2

export type State = 'not_paired' | 'mic_error' | 'card_error' | 'no_card' | 'card_full' | 'off' | 'starting' | 'recording'

/** The recorder's status. rec-12 to rec-14 send the first block on every read and notification. From rec-15
 *  (`v` 2) a read sends everything and a notification only the live part, which `mergeStatus` folds in. */
export interface Status {
  fw: string
  state: State
  want_on: boolean
  recording: boolean
  card_free_mb: number
  card_total_mb: number
  wifi: boolean
  ssid: string
  rssi: number
  networks: number
  clock_set: boolean
  battery_mv: number
  // rec-15 (v 2)
  v?: number
  name?: string
  /** Counts every start of the recorder: a new number means it restarted. */
  boot?: number
  /** Seconds into the current recording. */
  rec_s?: number
  /** While an update arrives: the percent received; -1 otherwise. */
  upd?: number
  /** True just before a restart the recorder chose (after an update, or going back). */
  restart?: boolean
  /** A new version waiting for the Mac to confirm it. */
  pending?: boolean
  /** True after the recorder went back to the version before (the new one didn't start). */
  rolled_back?: boolean
  // rec-24, a build with the owner's filler model (B20): the filler cue's setting and this recording's count
  filler_model?: boolean
  filler_on?: boolean
  fillers?: number
}

/** One recorder seen nearby or remembered. */
export interface Found { id: string; name: string; rssi?: number }

export type DropHandler = () => void

export interface Link {
  /** Ready the radio. Rejects when Bluetooth isn't allowed or there is none. */
  init(): Promise<void>
  enabled(): Promise<boolean>
  onEnabled(cb: (on: boolean) => void): Promise<void>
  openSettings(): Promise<void>
  /** Look for recorders nearby; `cb` once per recorder (and again when its signal changes). */
  scan(cb: (f: Found) => void): Promise<void>
  stopScan(): Promise<void>
  /** Whether the phone still knows this recorder (iOS keeps a list of the ones it has met). */
  known(id: string): Promise<boolean>
  connect(id: string, onDrop: DropHandler, timeoutMs: number): Promise<void>
  /** The first read after connecting may bring up the phone's code box: give it time. */
  status(timeoutMs?: number): Promise<Status>
  onStatus(cb: (s: Partial<Status>) => void): Promise<void>
  setRecording(on: boolean): Promise<void>
  /** The filler cue (rec-24): "you know" on the recorder's screen while it records. */
  setFiller(on: boolean): Promise<void>
  networks(): Promise<string[]>
  addNetwork(ssid: string, password: string): Promise<string[]>
  removeNetwork(ssid: string): Promise<string[]>
  disconnect(): Promise<void>
}

export function parseStatus(text: string): Status {
  const s = JSON.parse(text) as Status
  if (typeof s.state !== 'string' || typeof s.fw !== 'string') throw new Error('not a status from the recorder')
  return s
}

/** A live notification: a whole status (rec-14 and before) or only the live part (rec-15). */
export function parseLive(text: string): Partial<Status> {
  const s = JSON.parse(text) as Partial<Status>
  if (typeof s.state !== 'string') throw new Error('not a status from the recorder')
  return s
}

export const mergeStatus = (base: Status | null, live: Partial<Status>): Status | null =>
  base ? { ...base, ...live } : null

export function parseNetworks(text: string): string[] {
  const v = JSON.parse(text)
  if (!Array.isArray(v) || !v.every((x) => typeof x === 'string')) throw new Error('not a network list from the recorder')
  return v
}

// The recorder's own limits (octo_settings.h): checked before sending so the person is told why.
export function networkProblem(ssid: string, password: string): string | null {
  const bytes = new TextEncoder().encode(ssid).length
  if (!ssid.trim()) return 'Type the network\'s name.'
  if (bytes > 32) return 'That name is too long for the recorder.'
  if (ssid.includes('\n') || password.includes('\n')) return 'No line breaks in a name or password.'
  if (password && (password.length < 8 || password.length > 63)) return 'Wi-Fi passwords are 8 to 63 characters. Leave it empty for an open network.'
  return null
}

export const addCommand = (ssid: string, password: string) => `add\n${ssid}\n${password}`
export const removeCommand = (ssid: string) => `del\n${ssid}`

// 16 kHz, 16-bit mono: 32,000 bytes a second, about 110 MiB an hour.
export const hoursLeft = (freeMb: number) => (freeMb * 1048576) / (32000 * 3600)

/** Room on the card, as time to record: "About 10 days". */
export function roomText(freeMb: number): string {
  const h = hoursLeft(freeMb)
  if (h >= 48) return `About ${Math.floor(h / 24)} days`
  if (h >= 1) return `About ${Math.floor(h)} hour${Math.floor(h) === 1 ? '' : 's'}`
  return 'Under an hour'
}

/** The headline: the recorder's own words where its screen has them, so a person can look from one to the other. */
export const STATE_TEXT: Record<State, string> = {
  not_paired: 'Set me up',
  mic_error: 'Mic problem',
  card_error: 'Card error',
  no_card: 'No SD card',
  card_full: 'Card full',
  off: 'Stopped',
  starting: 'Starting',
  recording: 'Recording',
}

export const NEEDS_YOU: State[] = ['not_paired', 'mic_error', 'card_error', 'no_card', 'card_full']

export function signal(rssi: number): string {
  if (rssi >= -60) return 'good'
  if (rssi >= -72) return 'fair'
  return 'weak'
}

/** "12 min", "1 h 05 min" */
export function duration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  if (m < 1) return 'less than a minute'
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}

export type Trouble = 'cancelled' | 'timeout' | 'auth' | 'removed' | 'off' | 'not_allowed' | 'unsupported' | 'gone' | 'other'

/** What went wrong, from the plugin's or iOS's own message. "Removed pairing" is checked before anything
 *  that says "pair": that one needs the person to forget the recorder in Settings, not to type a code. */
export function trouble(e: unknown): Trouble {
  const m = e instanceof Error ? e.message : String(e)
  if (/removed pairing/i.test(m)) return 'removed'
  if (/cancel/i.test(m)) return 'cancelled'
  if (/permission denied|unauthori[sz]ed|not allowed/i.test(m)) return 'not_allowed'
  if (/unsupported|not available/i.test(m)) return 'unsupported'
  if (/not enabled|powered off|bluetooth.*off|disabled/i.test(m)) return 'off'
  if (/insufficient|authentication|encryption|not paired/i.test(m)) return 'auth'
  if (/timeout|timed out/i.test(m)) return 'timeout'
  if (/disconnected|not connected|failed to connect/i.test(m)) return 'gone'
  return 'other'
}
