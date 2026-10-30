<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import AppHeader from '../components/AppHeader.vue'
import RecordingOverlay from '../components/RecordingOverlay.vue'
import StepDetail from '../components/StepDetail.vue'
import StepList from '../components/StepList.vue'
import { init, state } from '../store'
import type { StepView } from '../types'

let stop: (() => void) | null = null
let alive = true
onMounted(async () => {
  try {
    const s = await init()
    if (!alive) { s(); return }  // left the page while it connected: close the stream now (M35)
    stop = s
  } catch (err) {
    state.error = `Couldn't reach the bench server: ${err instanceof Error ? err.message : err}`
  }
})
onUnmounted(() => { alive = false; stop?.() })

const selected = computed<StepView | null>(() => {
  const id = state.selected ?? state.catalog[0]?.id
  const live = state.run?.steps.find((s) => s.id === id) ?? (id ? state.lastKnown[id] : undefined)
  if (live) return live
  const c = state.catalog.find((s) => s.id === id)
  return c ? { id: c.id, title: c.title, needsUser: c.needs_user, state: 'queued', metrics: [], logs: [],
               summary: c.skip_reason ?? undefined } : null
})

const recording = computed(() => {
  const r = state.run
  return r?.prompt && r.recordingAt && r.prompt.phase !== 'ready' ? { prompt: r.prompt, at: r.recordingAt } : null
})
</script>

<template>
  <div class="app">
    <AppHeader />
    <div class="body">
      <StepList />
      <StepDetail v-if="selected" :key="selected.id" :step="selected" />
    </div>
    <RecordingOverlay v-if="recording" :prompt="recording.prompt" :started-at="recording.at" />
  </div>
</template>

<style scoped>
.app { flex-grow: 1; min-height: 0; display: flex; flex-direction: column; }
.body { flex-grow: 1; display: flex; min-height: 0; }
/* Narrow windows (a side pane, a small laptop split): pipeline above the detail, page scrolls. */
@media (max-width: 960px) {
  .app { height: auto; min-height: 100%; }
  .body { flex-direction: column; }
}
</style>
