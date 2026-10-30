<script setup lang="ts">
// Hold to record / hold to stop: the recorder's own gesture (1 s since rec-19), so the two teach each other. A tap (or
// VoiceOver's double tap) asks to confirm instead, so nothing depends on holding.
import { computed, onBeforeUnmount, ref } from 'vue'
import { haptic } from '../haptics'

// `stop` names what letting go does while recording ("stop and send" on Memos; "stop" on the recorder)
const props = defineProps<{ recording: boolean; disabled?: boolean; stop?: string }>()
const emit = defineEmits<{ (e: 'done'): void; (e: 'tap'): void }>()

const HOLD_MS = 1000
const TAP_MS = 300
const held = ref(0)
let downAt = 0
let raf = 0
let readyTold = false

const verb = computed(() => (props.recording ? props.stop ?? 'stop' : 'record'))
const label = computed(() => {
  if (!held.value) return `Hold to ${verb.value}`
  return held.value >= HOLD_MS ? `Let go to ${verb.value}` : `Keep holding to ${verb.value}`
})
const ring = computed(() => Math.min(1, held.value / HOLD_MS))

function frame() {
  held.value = performance.now() - downAt
  if (held.value >= HOLD_MS && !readyTold) { readyTold = true; haptic('medium') }
  raf = requestAnimationFrame(frame)
}

function down(e: PointerEvent) {
  if (props.disabled || e.button !== 0) return
  ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
  downAt = performance.now()
  readyTold = false
  haptic('light')
  raf = requestAnimationFrame(frame)
}

function up() {
  if (!downAt) return
  cancelAnimationFrame(raf)
  const t = performance.now() - downAt
  downAt = 0
  held.value = 0
  if (t >= HOLD_MS) emit('done')
  else if (t < TAP_MS) emit('tap')
}

function cancel() { cancelAnimationFrame(raf); downAt = 0; held.value = 0 }

function key(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (!props.disabled) emit('tap') }
}

onBeforeUnmount(() => cancelAnimationFrame(raf))
</script>

<template>
  <button type="button" class="hold" :class="{ 'is-rec': recording, holding: held > 0 }" :disabled="disabled"
    :aria-label="recording ? (stop ? `Hold to ${stop}` : 'Hold to stop recording') : 'Hold to record'" aria-describedby="hold-hint"
    @pointerdown="down" @pointerup="up" @pointercancel="cancel" @pointerleave="cancel" @keydown="key" @click.prevent>
    <svg class="ring" viewBox="0 0 28 28" aria-hidden="true">
      <circle cx="14" cy="14" r="12" class="track" />
      <circle cx="14" cy="14" r="12" class="fill" :style="{ strokeDashoffset: `${75.4 * (1 - ring)}` }" />
      <circle cx="14" cy="14" r="5" class="dot" />
    </svg>
    <span>{{ label }}</span>
  </button>
  <span id="hold-hint" class="visually-hidden">Hold for a second, or double-tap for options.</span>
</template>

<style scoped>
.hold {
  width: 100%; min-height: 56px; border-radius: 999px; display: flex; align-items: center; justify-content: center; gap: 12px;
  font: inherit; font-size: 1rem; font-weight: 600; border: 1px solid var(--accent); background: var(--accent); color: var(--accent-ink);
  touch-action: none; user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; cursor: pointer;
  transition: transform 100ms ease;
}
.hold.is-rec { background: var(--surface); color: var(--ink); border-color: var(--line-strong); }
.hold.holding { transform: scale(0.98); }
.hold:disabled { opacity: 0.5; cursor: default; }
.ring { width: 28px; height: 28px; transform: rotate(-90deg); flex-shrink: 0; }
.ring circle { fill: none; stroke-width: 2.5; }
.track { stroke: currentColor; opacity: 0.3; }
.fill { stroke: currentColor; stroke-dasharray: 75.4; }
.dot { fill: currentColor; stroke: none; }
.hold.is-rec .track { stroke: var(--rec); }
.hold.is-rec .fill { stroke: var(--rec); }
.hold.is-rec .dot { fill: var(--rec); }
@media (prefers-reduced-motion: reduce) { .hold.holding { transform: none; } }
</style>
