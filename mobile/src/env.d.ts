/// <reference types="vite/client" />
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

/** Stamped at build time (vite.config.ts): the commit this app was built from, and when. */
declare const __APP_BUILD__: { commit: string; at: string }
