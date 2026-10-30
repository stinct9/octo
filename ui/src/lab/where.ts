// Where this page runs: on the Mac itself, on a phone over the home Wi-Fi, or on a phone through Tailscale (away).
// The server decides what a phone may do (octo/phone_api.py PHONE_ROUTES); this only hides what it would refuse.

export type Where = 'mac' | 'home' | 'away'

export function whereFrom(hostname: string): Where {
  const h = hostname.toLowerCase()
  if (['127.0.0.1', 'localhost', '::1', '[::1]'].includes(h)) return 'mac'
  if (h.endsWith('.ts.net')) return 'away'
  const m = /^100\.(\d+)\.\d+\.\d+$/.exec(h)
  if (m && (Number(m[1]) & 0xc0) === 64) return 'away' // Tailscale's 100.64.0.0/10
  return 'home'
}

export const where: Where = typeof location !== 'undefined' ? whereFrom(location.hostname) : 'mac'
export const onMac = where === 'mac'
/** Precision work against the transcript (split, merge, move lines, undo, leave out, delete a part): the Mac. */
export const canEdit = onMac
/** What can't be undone (delete a conversation, forget someone, withdraw, rename): the Mac or the home Wi-Fi. */
export const canDelete = where !== 'away'
