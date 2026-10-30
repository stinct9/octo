import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// `npm run dev` serves the UI with hot reload and proxies /api to `bench serve` (port 8765).
// `npm run build` writes ui/dist, which `bench serve` hosts directly.
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    proxy: { '/api': { target: 'http://127.0.0.1:8765', changeOrigin: false } },
  },
  build: { outDir: 'dist', emptyOutDir: true },
})
