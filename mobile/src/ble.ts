// The real recorder, over Bluetooth (@capacitor-community/bluetooth-le). The first read of a value makes the
// phone pair: the recorder shows six digits and iOS asks for them.
//
// The plugin runs every call through one queue and cancels a connect when its timeout runs out, so the
// connection logic (connection.ts) makes short attempts in a loop rather than one long pending connect.
import { BleClient, dataViewToText, textToDataView } from '@capacitor-community/bluetooth-le'
import { CONTROL, NETWORKS, SERVICE, STATUS, addCommand, parseLive, parseNetworks, parseStatus, removeCommand } from './recorder'
import type { DropHandler, Found, Link, Status } from './recorder'

export class BleLink implements Link {
  private id = ''
  private ready = false

  async init() {
    if (this.ready) return
    await BleClient.initialize()
    this.ready = true
  }

  enabled() { return BleClient.isEnabled() }

  async onEnabled(cb: (on: boolean) => void) {
    await BleClient.startEnabledNotifications(cb)
  }

  openSettings() { return BleClient.openAppSettings() }

  async scan(cb: (f: Found) => void) {
    await BleClient.requestLEScan({ services: [SERVICE], allowDuplicates: false }, (r) => {
      cb({ id: r.device.deviceId, name: r.localName ?? r.device.name ?? 'Octo', rssi: r.rssi })
    })
  }

  async stopScan() { await BleClient.stopLEScan().catch(() => {}) }

  async known(id: string) {
    // Already connected (from an earlier run of the app), or remembered by iOS.
    const linked = await BleClient.getConnectedDevices([SERVICE]).catch(() => [])
    if (linked.some((d) => d.deviceId === id)) return true
    return (await BleClient.getDevices([id]).catch(() => [])).length > 0
  }

  async connect(id: string, onDrop: DropHandler, timeoutMs: number) {
    this.id = id
    await BleClient.connect(id, () => { if (this.id === id) onDrop() }, { timeout: timeoutMs })
  }

  async status(timeoutMs = 5000): Promise<Status> {
    return parseStatus(dataViewToText(await BleClient.read(this.id, SERVICE, STATUS, { timeout: timeoutMs })))
  }

  async onStatus(cb: (s: Partial<Status>) => void) {
    await BleClient.startNotifications(this.id, SERVICE, STATUS, (v) => {
      try {
        cb(parseLive(dataViewToText(v)))
      } catch {
        // Cut short (a notification holds at most the link's MTU): read it whole instead.
        this.status().then(cb, () => {})
      }
    })
  }

  async setRecording(on: boolean) {
    await BleClient.write(this.id, SERVICE, CONTROL, textToDataView(on ? 'on' : 'off'))
  }

  async setFiller(on: boolean) {
    await BleClient.write(this.id, SERVICE, CONTROL, textToDataView(on ? 'filler on' : 'filler off'))
  }

  async networks(): Promise<string[]> {
    return parseNetworks(dataViewToText(await BleClient.read(this.id, SERVICE, NETWORKS)))
  }

  async addNetwork(ssid: string, password: string): Promise<string[]> {
    await BleClient.write(this.id, SERVICE, NETWORKS, textToDataView(addCommand(ssid, password)))
    return this.networks()
  }

  async removeNetwork(ssid: string): Promise<string[]> {
    await BleClient.write(this.id, SERVICE, NETWORKS, textToDataView(removeCommand(ssid)))
    return this.networks()
  }

  async disconnect() {
    const id = this.id
    this.id = ''
    if (id) await BleClient.disconnect(id).catch(() => {})
  }
}
