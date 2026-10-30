// A made-up recorder and lab inside the app (product review P1-9): for someone just looking, and for App Review,
// which can't scan a Mac or hold a recorder. The same simulated recorder and lab as `?demo` in a browser, labelled
// "Made-up" on every screen. Nothing real is touched: no Bluetooth, no Mac, nothing saved but this switch.

import { Capacitor } from '@capacitor/core'

const KEY = 'octo.madeup'

export function madeUp(): boolean {
  try { return localStorage.getItem(KEY) === '1' } catch { return false }
}

/** Offered in the app only: a browser has ?demo. */
export function offerMadeUp(): boolean {
  return Capacitor.isNativePlatform() && !madeUp()
}

/** Switch it on or off; the app starts again either way, so nothing of one carries into the other. */
export function setMadeUp(on: boolean) {
  try { if (on) localStorage.setItem(KEY, '1'); else localStorage.removeItem(KEY) } catch { /* private mode */ }
  location.reload()
}
