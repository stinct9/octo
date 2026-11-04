<script setup lang="ts">
// The owner's fillers over time (docs/coach-plan.md §4.2): the line pooled over his last ~2,000 words in the
// accent with its band of ordinary variation, his first fortnight as a labelled reference, and "now" at the end.
// "latest" marks the line's end. A small faint dot per conversation shows the spread without covering the line (the owner, 3 Nov: on a phone the
// word-sized dots buried it). One measure, one axis; neutral colours (A4). Hover, a tap or the arrow keys show
// a conversation, with a link to it; Enter opens it. A table view sits beside it.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { bandPath, linear, linePath, nearest, niceMax, weekTicks, yTicks } from '../lab/chart'
import { day } from '../lab/format'
import { href } from '../lab/router'
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
  document.addEventListener('pointerdown', away)
})
onUnmounted(() => { ro?.disconnect(); document.removeEventListener('pointerdown', away) })

const H = 220
const narrow = computed(() => width.value < 480)
const M = computed(() => ({ l: 30, r: narrow.value ? 12 : 92, t: 14, b: 26 }))
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
const dates = computed(() => weekTicks(xDomain.value[0], xDomain.value[1], Math.floor((width.value - M.value.l - M.value.r) / 64)))
const short = (t: number) => new Date(t * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
const band = computed(() => bandPath(xs.value, pts.value.map((p) => y.value(p.pool.lo)), pts.value.map((p) => y.value(p.pool.hi))))
const path = computed(() => linePath(xs.value, pts.value.map((p) => y.value(p.pool.rate))))
const last = computed(() => pts.value[pts.value.length - 1])
// The first fortnight's label sits at the left, on the side of its line away from where the pooled line starts.
const baseAbove = computed(() => {
  const b = props.baseline
  if (!b) return true
  const early = pts.value.filter((p) => x.value(p.at) < M.value.l + 150).map((p) => p.pool.rate)
  return !early.length || early.reduce((a, v) => a + v, 0) / early.length < b.rate
})
// "latest" beside the line's end in the right margin; on a phone, inside the plot above or below the end, on the
// side away from the first fortnight's line. (Not "now": the number above the chart counts only words after the
// fortnight, the line pools the last ~2,000 words, so the two can differ.)
const endAt = computed(() => {
  const l = last.value
  if (!l) return null
  const cx = xs.value[pts.value.length - 1]
  const cy = y.value(l.pool.rate)
  if (!narrow.value) return { x: cx + 10, y: cy + 4, anchor: 'start' }
  const by = props.baseline ? y.value(props.baseline.rate) : -999
  const above = cy - 12, below = cy + 20
  const up = above - 10 > M.value.t && (Math.abs(above - 4 - by) >= Math.abs(below - 4 - by) || below > H - M.value.b)
  return { x: cx, y: up ? above : below, anchor: 'end' }
})

const active = ref(-1)
const pinned = ref(false)  // a tap or click keeps the conversation shown, so its link can be followed
const hit = ref<SVGRectElement | null>(null)
function at(e: PointerEvent) {
  const r = hit.value?.getBoundingClientRect()
  return r ? nearest(xs.value, M.value.l + (e.clientX - r.left)) : -1
}
function move(e: PointerEvent) { if (!pinned.value && e.pointerType === 'mouse') active.value = at(e) }
function pick(e: PointerEvent) { active.value = at(e); pinned.value = active.value >= 0 }
function leave() { if (!pinned.value) active.value = -1 }
function away(e: Event) { if (pinned.value && !box.value?.contains(e.target as Node)) { pinned.value = false; active.value = -1 } }
function key(e: KeyboardEvent) {
  const n = pts.value.length
  if (!n) return
  if (e.key === 'ArrowRight') { active.value = active.value < 0 ? 0 : Math.min(n - 1, active.value + 1); e.preventDefault() }
  else if (e.key === 'ArrowLeft') { active.value = active.value < 0 ? n - 1 : Math.max(0, active.value - 1); e.preventDefault() }
  else if (e.key === 'Enter' && active.value >= 0) emit('open', pts.value[active.value].session_id)
  else if (e.key === 'Escape') { active.value = -1; pinned.value = false }
}
const tip = computed(() => {
  const p = pts.value[active.value]
  if (!p) return null
  const left = narrow.value ? 0 : Math.min(Math.max(8, xs.value[active.value] - 120), width.value - 248)
  return { p, left }
})
const fmt = (v: number) => v.toFixed(1)
const words = (n: number) => n.toLocaleString('en-GB')
const summary = computed(() => {
  const l = last.value
  if (!l) return 'No conversations yet'
  const base = props.baseline ? `; first fortnight ${fmt(props.baseline.rate)}` : ''
  return `Fillers per 100 words over ${pts.value.length} conversations; latest ${fmt(l.pool.rate)} over your last ${words(l.pool.words)} words${base}`
})
</script>

<template>
  <div ref="box" class="trend">
    <svg :width="width" :height="H" :viewBox="`0 0 ${width} ${H}`" role="img" :aria-label="summary" tabindex="0"
         @keydown="key" @focus="active = active < 0 ? pts.length - 1 : active" @blur="pinned || (active = -1)">
      <g class="grid">
        <template v-for="v in ticks" :key="v">
          <line :x1="M.l" :x2="width - M.r" :y1="y(v)" :y2="y(v)" />
          <text class="tick" :x="M.l - 8" :y="y(v) + 4" text-anchor="end">{{ v }}</text>
        </template>
        <template v-for="d in dates" :key="d">
          <line class="xt" :x1="x(d)" :x2="x(d)" :y1="H - M.b" :y2="H - M.b + 4" />
          <text class="tick" :x="x(d)" :y="H - 6" text-anchor="middle">{{ short(d) }}</text>
        </template>
      </g>
      <circle v-for="(p, i) in pts" :key="p.session_id" class="dot" :cx="xs[i]" :cy="y(p.rate)" r="2.5" />
      <path class="band" :d="band" />
      <g v-if="baseline">
        <line class="base" :x1="M.l" :x2="width - M.r" :y1="y(baseline.rate)" :y2="y(baseline.rate)" />
        <text class="label halo" :x="M.l + 6" :y="y(baseline.rate) + (baseAbove ? -6 : 15)">first fortnight {{ fmt(baseline.rate) }}</text>
      </g>
      <path class="line" :d="path" />
      <line v-if="tip" class="cross" :x1="xs[active]" :x2="xs[active]" :y1="M.t" :y2="H - M.b" />
      <circle v-if="tip" class="dot on" :cx="xs[active]" :cy="y(tip.p.rate)" r="5" />
      <g v-if="last && endAt">
        <circle class="end" :cx="xs[pts.length - 1]" :cy="y(last.pool.rate)" r="5" />
        <text class="label strong halo" :x="endAt.x" :y="endAt.y" :text-anchor="endAt.anchor">latest {{ fmt(last.pool.rate) }}</text>
      </g>
      <rect ref="hit" class="hit" :x="M.l" :y="M.t" :width="Math.max(0, width - M.l - M.r)" :height="H - M.t - M.b"
            @pointermove="move" @pointerleave="leave" @pointerdown="pick" />
    </svg>
    <div v-if="tip" class="tip" :class="{ below: narrow, pinned }" :style="narrow ? undefined : { left: `${tip.left}px` }" role="status">
      <div class="t1">{{ tip.p.rate.toFixed(1) }} <span class="u">per 100 words</span></div>
      <div class="t2">{{ day(tip.p.at) }}<template v-if="tip.p.with.length"> · with {{ tip.p.with.join(', ') }}</template></div>
      <div class="t3">{{ tip.p.fillers }} in {{ words(tip.p.words) }} of your words</div>
      <div class="t3"><i class="key" />Line {{ fmt(tip.p.pool.rate) }} (band {{ fmt(tip.p.pool.lo) }}–{{ fmt(tip.p.pool.hi) }}) over your last {{ words(tip.p.pool.words) }} words</div>
      <a class="go" :href="href.conversation(tip.p.session_id)">Open the conversation →</a>
    </div>
  </div>
</template>

<style scoped>
.trend { position: relative; width: 100%; }
svg { display: block; overflow: visible; touch-action: pan-y; }
svg:focus-visible { outline: 2px solid var(--accent); outline-offset: 4px; border-radius: 6px; }
.grid line { stroke: var(--line); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
.label { fill: var(--ink-2); font-size: 12px; font-variant-numeric: tabular-nums; }
.label.strong { fill: var(--ink); font-weight: 600; font-size: 13px; }
/* a ring of the card's colour behind the text, so a label stays readable where it crosses the band or a dot */
.halo { paint-order: stroke; stroke: var(--surface); stroke-width: 4px; stroke-linejoin: round; pointer-events: none; }
.base { stroke: var(--ink-2); stroke-width: 1; stroke-dasharray: 4 3; opacity: 0.8; }
.band { fill: var(--accent); opacity: 0.18; }
.line { fill: none; stroke: var(--accent); stroke-width: 2.5; stroke-linejoin: round; stroke-linecap: round; }
.dot { fill: var(--muted); opacity: 0.5; }
.dot.on { fill: var(--ink); opacity: 1; stroke: var(--surface); stroke-width: 2; pointer-events: none; }
.end { fill: var(--accent); stroke: var(--surface); stroke-width: 2; pointer-events: none; }
.cross { stroke: var(--ink-2); stroke-width: 1; opacity: 0.5; pointer-events: none; }
.hit { fill: transparent; cursor: crosshair; }
.tip { position: absolute; top: 4px; width: 240px; pointer-events: none; background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.14); padding: 8px 10px; font-size: 12.5px; display: flex; flex-direction: column; gap: 2px; }
.tip.pinned { pointer-events: auto; }
/* on a phone the details sit under the chart instead of over the line */
.tip.below { position: static; width: auto; margin-top: 8px; box-shadow: none; }
.t1 { font-size: 16px; font-weight: 600; color: var(--ink); }
.u { font-size: 12px; font-weight: 400; color: var(--muted); }
.t2 { color: var(--ink-2); }
.t3 { color: var(--muted); }
.go { display: none; margin-top: 4px; color: var(--accent); min-height: 32px; align-items: center; }
.tip.pinned .go { display: inline-flex; }
.key { display: inline-block; width: 12px; height: 2px; background: var(--accent); vertical-align: middle; margin-right: 6px; border-radius: 1px; }
@media (prefers-color-scheme: dark) {
  .band { opacity: 0.26; }
  .dot { opacity: 0.45; }
}
</style>
