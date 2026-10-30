<script setup lang="ts">
import { computed, onUnmounted, ref } from 'vue'
import { clock, duration } from '../lib/format'
import { actions, busy, state } from '../store'

const now = ref(Date.now() / 1000)
const timer = setInterval(() => { now.value = Date.now() / 1000 }, 1000)
onUnmounted(() => clearInterval(timer))

const portName = computed(() => state.port?.replace('/dev/cu.', '') ?? '')

const waitingFor = computed(() => {
  const p = state.run?.prompt
  if (state.run?.state !== 'waiting_for_user' || !p) return ''
  const s = Math.max(0, Math.floor(now.value - p.at))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
})

const lastRun = computed(() => {
  const r = state.run
  if (!r || r.state !== 'idle' || !r.status) return null
  const label = { pass: 'Pass', warn: 'Warn', fail: 'Fail', cancelled: 'Cancelled', aborted: 'Aborted', skipped: 'Skipped' }[r.status]
  return { when: clock(r.startedAt), label, status: r.status, took: duration(r.finishedAt ? r.finishedAt - r.startedAt : undefined) }
})
</script>

<template>
  <header class="bar">
    <div v-if="!state.connected" class="chip chip-fail" role="status">Bench server not reachable — is <span class="mono">bench serve</span> running?</div>
    <div v-else-if="state.port" class="chip chip-pass" role="status">
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="currentColor" /></svg>
      XIAO ESP32S3 on <span class="mono small">{{ portName }}</span>
    </div>
    <div v-else class="chip chip-fail" role="status">
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="none" stroke="currentColor" stroke-width="1.6" /></svg>
      Board not found — check the USB-C cable
    </div>

    <div class="spacer" />

    <div v-if="state.error" class="error" role="alert">{{ state.error }}</div>

    <template v-if="busy">
      <div v-if="state.run?.state === 'waiting_for_user'" class="chip chip-warn" role="status">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" stroke-width="1.6" /><path d="M7 3.8V7l2 1.4" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" /></svg>
        Waiting for you · {{ waitingFor }}
      </div>
      <div v-else-if="state.run?.state === 'cancelling'" class="chip chip-warn" role="status">Cancelling at the next safe point…</div>
      <div v-else class="status-text" role="status">Running…</div>
      <button type="button" class="btn" :disabled="state.run?.state === 'cancelling'" @click="actions.cancel">Cancel run</button>
    </template>
    <template v-else>
      <div v-if="lastRun" class="status-text">
        Last run {{ lastRun.when }} · <strong :class="lastRun.status">{{ lastRun.label }}</strong><template v-if="lastRun.took"> · {{ lastRun.took }}</template>
      </div>
      <button type="button" class="btn btn-primary" :disabled="!state.port || !state.connected" @click="actions.runAll">
        <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M3 1.5v11l9-5.5z" fill="currentColor" /></svg>
        Run all
      </button>
    </template>
  </header>
</template>

<style scoped>
.bar {
  height: 64px; padding: 0 28px; display: flex; align-items: center; gap: 20px;
  border-bottom: 1px solid var(--line); background: var(--surface); flex-shrink: 0;
}
.brand { font-size: 17px; font-weight: 500; letter-spacing: -0.2px; }
.brand span { color: var(--accent); }
.spacer { flex-grow: 1; }
.chip {
  display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 999px;
  font-size: 13px; font-weight: 500; white-space: nowrap;
}
@media (max-width: 960px) {
  .bar { height: auto; min-height: 64px; flex-wrap: wrap; padding: 10px 16px; gap: 10px 16px; }
}
.chip-pass { background: var(--pass-soft); color: var(--pass); }
.chip-warn { background: var(--warn-soft); color: var(--warn); font-weight: 600; }
.chip-fail { background: var(--fail-soft); color: var(--fail); }
.small { font-size: 12px; }
.status-text { font-size: 13px; color: var(--muted); }
.error { font-size: 13px; color: var(--fail); max-width: 360px; }
</style>
