<script setup lang="ts">
import { computed } from 'vue'
import { api } from '../api'
import type { Levels } from '../types'

const props = defineProps<{ levels: Levels; recording?: string }>()
const FLOOR = -80
const HEIGHT = 180

const total = computed(() => props.levels.seconds || props.levels.levels.length * props.levels.window_s)
const pct = (t: number) => `${Math.min(100, Math.max(0, (t / total.value) * 100))}%`
const bars = computed(() =>
  props.levels.levels.map((d, i) => ({
    h: Math.max(3, Math.round(((d - FLOOR) / -FLOOR) * HEIGHT)),
    speech: i * props.levels.window_s >= props.levels.speech_window[0],
    label: `${(i * props.levels.window_s).toFixed(2)} s: ${d} dBFS`,
  })),
)
const ticks = computed(() => [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(total.value * f * 10) / 10))
</script>

<template>
  <section class="card chart-card" aria-label="Recording levels">
    <div class="legend">
      <span class="strong">Level per {{ levels.window_s * 1000 }} ms</span>
      <span class="key"><span class="swatch quiet" />background window</span>
      <span class="key"><span class="swatch speech" />speech window</span>
      <span class="key"><span class="cue-swatch" />{{ levels.cue_at !== null ? `Glass cue at ${levels.cue_at} s` : 'Glass cue not heard' }}</span>
    </div>
    <div class="plot" :style="{ height: `${HEIGHT}px` }">
      <div class="window quiet" :style="{ left: pct(levels.quiet_window[0]), width: `calc(${pct(levels.quiet_window[1])} - ${pct(levels.quiet_window[0])})` }" />
      <div class="window speech" :style="{ left: pct(levels.speech_window[0]), right: '0' }" />
      <div v-if="levels.cue_at !== null" class="cue" :style="{ left: pct(levels.cue_at) }" />
      <div class="bars">
        <div v-for="(b, i) in bars" :key="i" class="bar" :class="{ speech: b.speech }" :style="{ height: `${b.h}px` }" :title="b.label" />
      </div>
    </div>
    <div class="mono ticks"><span v-for="t in ticks" :key="t">{{ t }} s</span></div>
    <audio v-if="recording" class="player" controls preload="none" :src="api.recordingUrl(recording)">
      Your browser can't play the recording.
    </audio>
  </section>
</template>

<style scoped>
.chart-card { border-radius: 16px; padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; }
.legend { display: flex; flex-wrap: wrap; align-items: center; gap: 16px; font-size: 13px; color: var(--muted); }
.strong { font-weight: 600; color: var(--ink); }
.key { display: flex; align-items: center; gap: 6px; }
.swatch { width: 12px; height: 12px; border-radius: 3px; }
.swatch.quiet, .window.quiet { background: var(--chart-quiet); }
.swatch.speech, .window.speech { background: var(--accent-soft); }
.cue-swatch { width: 3px; height: 12px; background: var(--warn); }
.plot { position: relative; }
.window { position: absolute; top: 0; bottom: 0; border-radius: 6px; }
.cue { position: absolute; top: 0; bottom: 0; width: 3px; margin-left: -1px; background: var(--warn); z-index: 1; }
.bars { position: absolute; inset: 0 2px; display: flex; align-items: flex-end; gap: 3px; z-index: 2; }
.bar { flex-grow: 1; background: var(--chart-bar); border-radius: 3px 3px 0 0; }
.bar.speech { background: var(--accent); }
.ticks { display: flex; justify-content: space-between; font-size: 11px; color: var(--muted); }
.player { width: 100%; height: 40px; }
</style>
