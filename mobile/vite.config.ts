import { execSync } from 'node:child_process'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Which code this build is (src/version.ts): the commit, "+" when built with changes not yet committed, and when.
function build() {
  const git = (cmd: string) => { try { return execSync(`git ${cmd}`, { cwd: __dirname }).toString().trim() } catch { return '' } }
  const commit = git('rev-parse --short HEAD') || 'dev'
  const dirty = git('status --porcelain -- .') !== ''
  return { commit: commit + (dirty ? '+' : ''), at: new Date().toISOString() }
}

export default defineConfig({
  plugins: [vue()],
  base: './',  // Capacitor serves the build from the app bundle
  define: { __APP_BUILD__: JSON.stringify(build()) },
  // The recorder's drawing code lives beside its C (firmware/recorder/octo_display.js), outside this folder.
  server: { host: '127.0.0.1', port: 5181, fs: { allow: ['..'] } },
})
