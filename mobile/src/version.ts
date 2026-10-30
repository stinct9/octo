// The app's version, for the About sheet and the line under the recorder (8 Oct: "for clarity"): the version
// iOS knows it by, and the code it was built from, which tells one test build from the next.
import { App } from '@capacitor/app'
import { Capacitor } from '@capacitor/core'

export interface AppVersion { version: string; build: string; commit: string; at: string }

const stamp = typeof __APP_BUILD__ !== 'undefined' ? __APP_BUILD__ : { commit: 'dev', at: '' }

export async function appVersion(): Promise<AppVersion> {
  let version = '', build = ''
  if (Capacitor.isNativePlatform()) {
    try { ({ version, build } = await App.getInfo()) } catch { /* not told */ }
  }
  return { version: version || 'web', build, commit: stamp.commit, at: stamp.at }
}

/** "1.0 (3) · a1b2c3d, 8 Oct" */
export function versionText(v: AppVersion): string {
  const when = v.at ? new Date(v.at).toLocaleDateString([], { day: 'numeric', month: 'short' }) : ''
  return `${v.version}${v.build ? ` (${v.build})` : ''} · ${v.commit}${when ? `, ${when}` : ''}`
}
