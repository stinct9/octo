import { describe, expect, it } from 'vitest'
import { FakeLink } from './fake'
import { addCommand, duration, hoursLeft, mergeStatus, networkProblem, parseLive, parseNetworks, parseStatus, removeCommand, roomText, trouble } from './recorder'

describe('what goes to the recorder', () => {
  it('writes network changes as the firmware reads them (docs/recorder-ble.md)', () => {
    expect(addCommand('My Home 5G', 'secret pass!')).toBe('add\nMy Home 5G\nsecret pass!')
    expect(addCommand('Cafe', '')).toBe('add\nCafe\n')
    expect(removeCommand('Cafe')).toBe('del\nCafe')
  })
  it('checks a network the way the recorder will', () => {
    expect(networkProblem('Home', 'password1')).toBeNull()
    expect(networkProblem('Cafe', '')).toBeNull() // open
    expect(networkProblem('', 'password1')).toMatch(/name/)
    expect(networkProblem('x'.repeat(33), 'password1')).toMatch(/too long/)
    expect(networkProblem('Home', 'short')).toMatch(/8 to 63/)
    expect(networkProblem('Home\nx', 'password1')).toMatch(/line breaks/)
  })
})

describe('what comes back', () => {
  it('reads a status and a network list, and refuses anything else', () => {
    const s = parseStatus('{"fw":"rec-12","state":"recording","want_on":true,"recording":true,"card_free_mb":28120,"card_total_mb":29600,"wifi":true,"ssid":"Home","rssi":-61,"networks":2,"clock_set":true,"battery_mv":-1}')
    expect(s.state).toBe('recording')
    expect(() => parseStatus('{"x":1}')).toThrow()
    expect(parseNetworks('["Home","Office"]')).toEqual(['Home', 'Office'])
    expect(() => parseNetworks('{"a":1}')).toThrow()
  })
  it('turns free card space into time to record, in words', () => {
    expect(Math.round(hoursLeft(28120))).toBe(256) // about 110 MiB an hour
    expect(roomText(28120)).toBe('About 10 days')
    expect(roomText(500)).toBe('About 4 hours')
    expect(roomText(50)).toBe('Under an hour')
    expect(duration(30)).toBe('less than a minute')
    expect(duration(12 * 60 + 5)).toBe('12 min')
    expect(duration(65 * 60)).toBe('1 h 05 min')
  })
  it('folds a short live value (rec-15) into the last full status', () => {
    const full = parseStatus('{"v":2,"fw":"rec-15","state":"off","want_on":false,"recording":false,"card_free_mb":28120,"card_total_mb":29600,"wifi":true,"ssid":"Home","rssi":-61,"networks":2,"clock_set":true,"battery_mv":-1,"boot":45,"upd":-1}')
    const live = parseLive('{"v":2,"fw":"rec-15","boot":45,"state":"recording","want_on":true,"recording":true,"rec_s":3,"upd":-1,"wifi":true,"rssi":-58}')
    expect(mergeStatus(full, live)).toMatchObject({ state: 'recording', card_free_mb: 28120, ssid: 'Home', rssi: -58, rec_s: 3 })
    expect(mergeStatus(null, live)).toBeNull()
  })
  it('names what went wrong from iOS and the plugin', () => {
    expect(trouble(new Error('Peer removed pairing information'))).toBe('removed')  // before anything with "pair"
    expect(trouble(new Error('Authentication is insufficient.'))).toBe('auth')
    expect(trouble(new Error('Read timeout.'))).toBe('timeout')
    expect(trouble(new Error('Connection timeout.'))).toBe('timeout')
    expect(trouble(new Error('BLE permission denied'))).toBe('not_allowed')
    expect(trouble(new Error('BLE unsupported'))).toBe('unsupported')
    expect(trouble(new Error('Bluetooth not enabled'))).toBe('off')
    expect(trouble(new Error('requestDevice cancelled.'))).toBe('cancelled')
  })
})

describe('the simulated recorder keeps the firmware rules', () => {
  it('newest first, five at most, bad ones ignored', async () => {
    const f = new FakeLink(0)
    await f.connect('fake', () => {}, 100)
    for (const n of ['a', 'b', 'c', 'd', 'e']) await f.addNetwork(n, 'password1')
    expect(await f.networks()).toEqual(['e', 'd', 'c', 'b', 'a'])
    expect(await f.addNetwork('Home', 'short')).toEqual(['e', 'd', 'c', 'b', 'a'])
    expect(await f.addNetwork('a', 'password2')).toEqual(['a', 'e', 'd', 'c', 'b'])
    expect(await f.removeNetwork('e')).toEqual(['a', 'd', 'c', 'b'])
    await f.setRecording(false)
    expect((await f.status()).state).toBe('off')
  })
})
