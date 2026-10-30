<script setup lang="ts">
import { formatValue, previousNote } from '../lib/format'
import type { Metric } from '../types'

defineProps<{ metrics: Metric[]; columns?: number }>()
const VERDICT_WORD = { pass: 'ok', warn: 'check', fail: 'fail' } as const
</script>

<template>
  <div class="grid" :style="{ gridTemplateColumns: `repeat(${columns ?? 3}, minmax(0, 1fr))` }">
    <div v-for="m in metrics" :key="m.key" class="card metric">
      <div class="label">{{ m.label }}</div>
      <div class="value-row">
        <span class="value" :class="{ long: String(m.value).length > 14 }">{{ formatValue(m.value) }}</span>
        <span v-if="m.unit" class="unit">{{ m.unit }}</span>
        <span v-if="m.verdict && m.verdict !== 'pass'" class="verdict" :class="m.verdict">{{ VERDICT_WORD[m.verdict] }}</span>
      </div>
      <div v-if="previousNote(m)" class="note">{{ previousNote(m) }}</div>
    </div>
  </div>
</template>

<style scoped>
.grid { display: grid; gap: 12px; }
@media (max-width: 960px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; } }
.metric { padding: 14px 16px; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.label { font-size: 13px; color: var(--muted); }
.value-row { display: flex; align-items: baseline; gap: 6px; min-width: 0; }
.value { font-size: 24px; font-weight: 600; letter-spacing: -0.4px; overflow-wrap: anywhere; }
.value.long { font-size: 15px; font-weight: 500; letter-spacing: 0; line-height: 1.4; }
.unit { font-size: 14px; color: var(--muted); }
.verdict { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.4px; }
.note { font-size: 12px; color: var(--muted); }
</style>
