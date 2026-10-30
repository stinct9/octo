import type { CapacitorConfig } from '@capacitor/cli'

// appId: change to your own reverse-DNS name when you register the app with Apple (product plan 4.5).
const config: CapacitorConfig = {
  appId: 'app.octo.recorder',
  appName: 'Octo',
  webDir: 'dist',
  plugins: {
    BluetoothLe: {
      displayStrings: { scanning: 'Looking for your recorder…', cancel: 'Cancel', availableDevices: 'Recorders nearby', noDeviceFound: 'No recorder found' },
    },
  },
}

export default config
