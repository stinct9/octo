// The folded parts of a page, remembered on this device.

export function remembered(key: string, fallback: boolean): boolean {
  try { const v = localStorage.getItem(key); return v === null ? fallback : v === '1' } catch { return fallback }
}

export function remember(key: string, on: boolean) { try { localStorage.setItem(key, on ? '1' : '0') } catch { /* private window */ } }
