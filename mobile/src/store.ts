// Where the app remembers its recorder: Capacitor Preferences (UserDefaults on iOS; this app's own storage).
// Only the recorder's identifier on this phone, its name and version, and its last status: nothing personal.
import { Preferences } from '@capacitor/preferences'
import type { Remembered, Store } from './connection'

const KEY = 'octo.recorder'

export class PrefsStore implements Store {
  async load(): Promise<Remembered | null> {
    try {
      const { value } = await Preferences.get({ key: KEY })
      return value ? (JSON.parse(value) as Remembered) : null
    } catch {
      return null
    }
  }

  async save(r: Remembered | null) {
    try {
      if (r) await Preferences.set({ key: KEY, value: JSON.stringify(r) })
      else await Preferences.remove({ key: KEY })
    } catch { /* the next connection saves it again */ }
  }
}
