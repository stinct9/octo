<script setup lang="ts">
import type { StepState } from '../types'

withDefaults(defineProps<{ state: StepState; size?: number }>(), { size: 20 })
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 20 20" aria-hidden="true" class="icon">
    <template v-if="state === 'pass'">
      <circle cx="10" cy="10" r="9" fill="var(--pass)" />
      <path d="M6 10.2l2.6 2.6L14 7.6" stroke="var(--surface)" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round" />
    </template>
    <template v-else-if="state === 'warn'">
      <circle cx="10" cy="10" r="9" fill="var(--warn)" />
      <path d="M10 5.5v5.5M10 14v.4" stroke="var(--surface)" stroke-width="2.2" stroke-linecap="round" />
    </template>
    <template v-else-if="state === 'fail'">
      <circle cx="10" cy="10" r="9" fill="var(--fail)" />
      <path d="M7 7l6 6M13 7l-6 6" stroke="var(--surface)" stroke-width="2" stroke-linecap="round" />
    </template>
    <template v-else-if="state === 'running'">
      <circle cx="10" cy="10" r="8" fill="none" stroke="var(--accent-soft)" stroke-width="3" />
      <path class="spin" d="M10 2a8 8 0 0 1 8 8" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round" />
    </template>
    <template v-else-if="state === 'queued'">
      <circle cx="10" cy="10" r="8" fill="none" stroke="var(--faint)" stroke-width="1.6" stroke-dasharray="3 3" />
    </template>
    <template v-else>
      <!-- skipped / cancelled / aborted -->
      <circle cx="10" cy="10" r="8" fill="none" stroke="var(--muted)" stroke-width="1.6" />
      <path d="M6.5 10h7" stroke="var(--muted)" stroke-width="1.8" stroke-linecap="round" />
    </template>
  </svg>
</template>

<style scoped>
.icon { flex-shrink: 0; }
.spin { transform-origin: 10px 10px; animation: spin 0.9s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin { animation: none; } }
</style>
