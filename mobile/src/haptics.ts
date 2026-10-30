// Haptics only ever repeat what the screen says (docs/phone/design-review.md §7): one success for the first
// connection and for things the person did; nothing for routine reconnects or status ticks.
import { Capacitor } from '@capacitor/core'
import { Haptics, ImpactStyle, NotificationType } from '@capacitor/haptics'

export type Feel = 'light' | 'medium' | 'success' | 'warning' | 'error'

export function haptic(feel: Feel) {
  if (!Capacitor.isNativePlatform()) return
  const p = feel === 'light' ? Haptics.impact({ style: ImpactStyle.Light })
    : feel === 'medium' ? Haptics.impact({ style: ImpactStyle.Medium })
    : Haptics.notification({ type: { success: NotificationType.Success, warning: NotificationType.Warning, error: NotificationType.Error }[feel] })
  p.catch(() => {})
}
