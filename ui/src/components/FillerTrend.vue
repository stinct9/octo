<script setup lang="ts">
// The owner's fillers over time (docs/coach-plan.md §4.2): a grey dot per conversation (bigger with more of
// his words), the line pooled over his last ~2,000 words in the accent with a faint band for ordinary
// variation, and his first fortnight as a hairline. One measure, one axis; neutral colours (A4).
// Hover or arrow keys show a conversation; Enter or a click opens it. A table view sits beside it.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { bandPath, dayTicks, linear, linePath, nearest, niceMax, radius, yTicks } from '../lab/chart'
import { day } from '../lab/format'
import type { FillerPoint, Pooled } from '../lab/types'

const props = defineProps<{ points: FillerPoint[]; line: ({ at: number | null } & Pooled)[]; baseline: Pooled | null; windowWords: number }>()
const emit = defineEmits<{ open: [sessionId: string] }>()

const box = ref<HTMLElement | null>(null)
const width = ref(640)
let ro: ResizeObserver | undefined
onMounted(() => {
  if (!box.value) return
  width.value = box.value.clientWidth || 640
  ro = new ResizeObserver(([e]) => { width.value = Math.max(280, Math.round(e.contentRect.width)) })
  ro.observe(box.value)
})
onUnmounted(() => ro?.disconnect())

const H = 220
const M = computed(() => ({ l: 30, r: width.value < 480 ? 12 : 84, t: 14, b: 26 }))
// Conversations with a time and words, oldest first, each with its pooled line value.
const pts = computed(() => props.points.map((p, i) => ({ ...p, pool: props.line[i] }))
  .filter((p) => p.at !== null && p.rate !== null) as (FillerPoint & { at: number; rate: number; pool: { at: number | null } & Pooled })[])
const xDomain = computed(() => {
  if (!pts.value.length) return [0, 1]
  const a = pts.value[0].at
  const b = pts.value[pts.value.length - 1].at
  const pad = Math.max(12 * 3600, (b - a) * 0.04)
  return [a - pad, b + pad]
})
const yMax = computed(() => niceMax(Math.max(0, ...pts.value.map((p) => Math.max(p.rate, p.pool.hi)), props.baseline?.hi ?? 0)))
const x = computed(() => linear(xDomain.value[0], xDomain.value[1], M.value.l, width.value - M.value.r))
const y = computed(() => linear(0, yMax.value, H - M.value.b, M.value.t))
const xs = computed(() => pts.value.map((p) => x.value(p.at)))
const ticks = computed(() => yTicks(yMax.value))
const days = computed(() => dayTicks(xDomain.value[0], xDomain.value[1], Math.max(2, Math.floor((width.value - 120) / 90))))
const band = computed(() => bandPath(xs.value, pts.value.map((p) => y.value(p.pool.lo)), pts.value.map((p) => y.value(p.pool.hi))))
const path = computed(() => linePath(xs.value, pts.value.map((p) => y.value(p.pool.rate))))
const last = computed(() => pts.value[pts.value.length - 1])

const active = ref(-1)
const hit = ref<SVGRectElement | null>(null)
function move(e: PointerEvent) {
  const r = hit.value?.getBoundingClientRect()
  if (!r) return
  active.value = nearest(xs.value, M.value.l + (e.clientX - r.left))
}
function key(e: KeyboardEvent) {
  const n = pts.value.length
  if (!n) return
  if (e.key === 'ArrowRight') { active.value = active.value < 0 ? 0 : Math.min(n - 1, active.value + 1); e.preventDefault() }
  else if (e.key === 'ArrowLeft') { active.value = active.value < 0 ? n - 1 : Math.max(0, active.value - 1); e.preventDefault() }
  else if (e.key === 'Enter' && active.value >= 0) emit('open', pts.value[active.value].session_id)
  else if (e.key === 'Escape') active.value = -1
}
const tip = computed(() => {
  const p = pts.value[active.value]
  if (!p) return null
  const left = Math.min(Math.max(8, xs.value[active.value] - 120), width.value - 248)
  return { p, left }
})
const fmt = (v: number) => v.toFixed(1)
const words = (n: number) => n.toLocaleString('en-GB')
const summary = computed(() => {
  const l = last.value
  return l ? `Fillers per 100 words over ${pts.value.length} conversations; now ${fmt(l.pool.rate)}` : 'No conversations yet'
})
</script>

<template>
  <div ref="box" class="trend">
    <svg :width="width" :height="H" :viewBox="`0 0 ${width} ${H}`" role="img" :aria-label="summary" tabindex="0"
         @keydown="key" @focus="active = active < 0 ? pts.length - 1 : active" @blur="active = -1">
      <g class="grid">
        <template v-for="v in ticks" :key="v">
          <line :x1="M.l" :x2="width - M.r" :y1="y(v)" :y2="y(v)" />
          <text class="tick" :x="M.l - 8" :y="y(v) + 4" text-anchor="end">{{ v }}</text>
        </template>
        <text v-for="d in days" :key="d" class="tick" :x="x(d)" :y="H - 6" text-anchor="middle">{{ day(d) }}</text>
      </g>
      <g v-if="baseline">
        <line class="base" :x1="M.l" :x2="width - M.r" :y1="y(baseline.rate)" :y2="y(baseline.rate)" />
        <text v-if="width >= 480" class="label" :x="width - M.r + 6" :y="y(baseline.rate) + 4">first fortnight {{ fmt(baseline.rate) }}</text>
      </g>
      <path class="band" :d="band" />
      <path class="line" :d="path" />
      <line v-if="tip" class="cross" :x1="xs[active]" :x2="xs[active]" :y1="M.t" :y2="H - M.b" />
      <circle v-for="(p, i) in pts" :key="p.session_id" class="dot" :class="{ on: i === active }" :cx="xs[i]" :cy="y(p.rate)"
              :r="radius(p.words)" @click="emit('open', p.session_id)" />
      <circle v-if="last" class="end" :cx="xs[pts.length - 1]" :cy="y(last.pool.rate)" r="4" />
      <rect ref="hit" class="hit" :x="M.l" :y="M.t" :width="Math.max(0, width - M.l - M.r)" :height="H - M.t - M.b"
            @pointermove="move" @pointerleave="active = -1" @click="active >= 0 && emit('open', pts[active].session_id)" />
    </svg>
    <div v-if="tip" class="tip" :style="{ left: `${tip.left}px` }" role="status">
      <div class="t1">{{ tip.p.rate.toFixed(1) }} <span class="u">per 100 words</span></div>
      <div class="t2">{{ day(tip.p.at) }}<template v-if="tip.p.with.length"> · with {{ tip.p.with.join(', ') }}</template></div>
      <div class="t3">{{ tip.p.fillers }} in {{ words(tip.p.words) }} of your words</div>
      <div class="t3"><i class="key" />Line {{ fmt(tip.p.pool.rate) }} (band {{ fmt(tip.p.pool.lo) }}–{{ fmt(tip.p.pool.hi) }}) over your last {{ words(tip.p.pool.words) }} words</div>
    </div>
  </div>
</template>

<style scoped>
.trend { position: relative; width: 100%; }
svg { display: block; overflow: visible; touch-action: pan-y; }
svg:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 6px; }
.grid line { stroke: var(--line); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.label { fill: var(--muted); font-size: 12px; }
.label.strong { fill: var(--ink); font-weight: 600; }
.base { stroke: var(--muted); stroke-width: 1; opacity: 0.7; }
.band { fill: var(--accent); opacity: 0.12; }
.line { fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.dot { fill: var(--chart-bar); stroke: var(--surface); stroke-width: 2; cursor: pointer; }
.dot.on { fill: var(--ink-2); }
.end { fill: var(--accent); stroke: var(--surface); stroke-width: 2; pointer-events: none; }
.cross { stroke: var(--ink-2); stroke-width: 1; opacity: 0.5; pointer-events: none; }
.hit { fill: transparent; cursor: crosshair; }
.tip { position: absolute; top: 4px; width: 240px; pointer-events: none; background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14); padding: 8px 10px; font-size: 12.5px; display: flex; flex-direction: column; gap: 2px; }
.t1 { font-size: 16px; font-weight: 600; color: var(--ink); }
.u { font-size: 12px; font-weight: 400; color: var(--muted); }
.t2 { color: var(--ink-2); }
.t3 { color: var(--muted); }
.key { display: inline-block; width: 12px; height: 2px; background: var(--accent); vertical-align: middle; margin-right: 6px; border-radius: 1px; }
</style>
