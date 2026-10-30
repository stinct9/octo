<script setup lang="ts">
// The recorder's screen on the phone: 128 x 64 pixels drawn by the recorder's own code, at a whole-number scale
// so they stay crisp, each a small square with a gap (the OLED's dot matrix) and its glow. Always dark, in light
// mode too: it is the recorder's screen, under the case's dark glass. Redrawn at the recorder's cadence (150 ms,
// 50 ms during a short animation), never in the background; one still frame with reduced motion.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { OD } from '../octo'
import type { Scene } from '../scenes'

const props = defineProps<{ scene: Scene; dim?: boolean; caption?: string }>()

const canvas = ref<HTMLCanvasElement | null>(null)
const box = ref<HTMLDivElement | null>(null)
const frame = OD.frame()
const reduce = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches
let began = performance.now()
let timer: ReturnType<typeof setTimeout> | undefined
let scale = 4

function size() {
  const el = canvas.value, b = box.value
  if (!el || !b) return
  const dpr = window.devicePixelRatio || 1
  scale = Math.max(2, Math.floor((b.clientWidth * dpr) / OD.W))
  el.width = OD.W * scale
  el.height = OD.H * scale
  el.style.width = `${(OD.W * scale) / dpr}px`
  el.style.height = `${(OD.H * scale) / dpr}px`
  draw()
}

function draw() {
  const el = canvas.value
  if (!el) return
  const ctx = el.getContext('2d')
  if (!ctx) return
  const ms = reduce ? (props.scene.still ?? 0) : performance.now() - began
  props.scene.draw(frame, ms)
  // Transparent ground (the glass behind is the screen's black), so only lit pixels glow.
  ctx.clearRect(0, 0, el.width, el.height)
  ctx.fillStyle = '#E8F4FF'
  const inset = Math.max(1, Math.round(scale * 0.1)), side = scale - 2 * inset
  for (let y = 0; y < OD.H; y++) for (let x = 0; x < OD.W; x++) if (OD.lit(frame, x, y)) ctx.fillRect(x * scale + inset, y * scale + inset, side, side)
}

function tick() {
  draw()
  if (reduce || document.hidden) return
  const fast = props.scene.anim !== undefined && performance.now() - began < props.scene.anim
  timer = setTimeout(tick, fast ? 50 : 150)
}

function restart() {
  clearTimeout(timer)
  began = performance.now()
  tick()
}

function visibility() { if (!document.hidden) { clearTimeout(timer); tick() } else clearTimeout(timer) }

let ro: ResizeObserver | undefined
onMounted(() => {
  size()
  ro = new ResizeObserver(size)
  if (box.value) ro.observe(box.value)
  document.addEventListener('visibilitychange', visibility)
  restart()
})
onBeforeUnmount(() => {
  clearTimeout(timer)
  ro?.disconnect()
  document.removeEventListener('visibilitychange', visibility)
})
watch(() => props.scene.key, restart)
watch(() => props.scene, () => { if (reduce) draw() })
</script>

<template>
  <figure class="panel" :class="{ dim }">
    <div ref="box" class="glass">
      <canvas ref="canvas" :role="scene.label ? 'img' : undefined" :aria-label="scene.label" :aria-hidden="scene.label ? undefined : 'true'"></canvas>
    </div>
    <figcaption v-if="caption">{{ caption }}</figcaption>
  </figure>
</template>

<style scoped>
.panel { margin: 0; display: flex; flex-direction: column; gap: 6px; }
.glass {
  background: #05090C; border-radius: 16px; box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.06);
  aspect-ratio: 2 / 1; max-height: 35vh; display: flex; align-items: center; justify-content: center; overflow: hidden;
}
canvas { display: block; filter: drop-shadow(0 0 2px rgba(232, 244, 255, 0.55)); transition: opacity 500ms ease; }
.dim canvas { opacity: 0.32; }
figcaption { font-size: 0.8rem; color: var(--muted); text-align: center; }
@media (prefers-contrast: more) { canvas { filter: none; } }
</style>
