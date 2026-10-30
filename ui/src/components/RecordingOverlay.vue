<script setup lang="ts">
// Full-screen cue while the board records. Driven by the server's prompt events
// (silent at 0 s, speak at 3 s, done at 10 s), animated locally between them.
import { computed, onUnmounted, ref } from 'vue'
import type { Prompt } from '../types'

const props = defineProps<{ prompt: Prompt; startedAt: number }>()
const RECORD_S = 10
const SPEAK_AT = 3
const R = 136
const CIRC = 2 * Math.PI * R

const now = ref(Date.now() / 1000)
let raf = 0
const tick = () => { now.value = Date.now() / 1000; raf = requestAnimationFrame(tick) }
raf = requestAnimationFrame(tick)
onUnmounted(() => cancelAnimationFrame(raf))

const elapsed = computed(() => Math.min(RECORD_S, Math.max(0, now.value - props.startedAt)))
const speaking = computed(() => props.prompt.phase === 'speak' || elapsed.value >= SPEAK_AT)
const finished = computed(() => props.prompt.phase === 'done' || props.prompt.phase === 'transfer')
const headline = computed(() => (finished.value ? 'Done' : speaking.value ? 'Speak now' : 'Stay silent'))
const sub = computed(() => {
  if (finished.value) return 'Saving the recording…'
  if (!speaking.value) return `talk in ${Math.ceil(SPEAK_AT - elapsed.value)} s`
  return `${(RECORD_S - elapsed.value).toFixed(1)} s left`
})
const dashOffset = computed(() => CIRC * (1 - elapsed.value / RECORD_S))
const speakFill = computed(() => `${Math.max(0, Math.min(1, (elapsed.value - SPEAK_AT) / (RECORD_S - SPEAK_AT))) * 100}%`)
const cursor = computed(() => `${(elapsed.value / RECORD_S) * 100}%`)
</script>

<template>
  <div class="stage" role="dialog" aria-modal="true" aria-live="assertive" :aria-label="headline">
    <div class="rec"><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><circle cx="6" cy="6" r="5" fill="#E5534B" /></svg>Recording on the board</div>

    <div class="ring">
      <svg width="300" height="300" viewBox="0 0 300 300" aria-hidden="true">
        <circle cx="150" cy="150" :r="R" fill="none" stroke="var(--stage-track)" stroke-width="14" />
        <circle cx="150" cy="150" :r="R" fill="none" stroke="var(--stage-fill)" stroke-width="14" stroke-linecap="round"
                :stroke-dasharray="CIRC" :stroke-dashoffset="dashOffset" transform="rotate(-90 150 150)" />
      </svg>
      <div class="ring-text">
        <div class="headline" :class="{ quiet: !speaking && !finished }">{{ headline }}</div>
        <div class="mono sub">{{ sub }}</div>
      </div>
    </div>

    <div class="timeline" aria-hidden="true">
      <div class="track">
        <div class="seg silent" />
        <div class="seg speak"><div class="speak-fill" :style="{ width: speakFill }" /></div>
        <div class="cursor" :style="{ left: cursor }" />
      </div>
      <div class="mono labels"><span>0 s · Ping · stay silent</span><span>3 s · Glass · talk</span><span>10 s · Hero · done</span></div>
    </div>

    <div class="foot"><span>The board's orange LED is on while it records</span><span class="dot" /><span>Cancel waits until the recording is saved</span></div>
  </div>
</template>

<style scoped>
.stage {
  position: fixed; inset: 0; z-index: 10; background: var(--stage); color: var(--stage-ink);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 44px;
}
.rec { display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 600; letter-spacing: 0.8px; text-transform: uppercase; color: #F2A0A0; }
.ring { position: relative; width: 300px; height: 300px; }
.ring svg { position: absolute; inset: 0; }
.ring-text { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; }
.headline { font-size: 54px; font-weight: 600; letter-spacing: -1px; }
.headline.quiet { font-size: 46px; color: var(--stage-muted); }
.sub { font-size: 20px; color: var(--stage-muted); }
.timeline { width: min(760px, 86vw); display: flex; flex-direction: column; gap: 10px; }
.track { position: relative; height: 16px; display: flex; gap: 4px; }
.seg { height: 16px; border-radius: 8px; }
.seg.silent { width: 30%; background: #3A4E60; }
.seg.speak { flex-grow: 1; background: var(--stage-track); position: relative; overflow: hidden; }
.speak-fill { position: absolute; inset: 0 auto 0 0; background: var(--stage-fill); }
.cursor { position: absolute; top: -6px; width: 3px; height: 28px; margin-left: -1px; border-radius: 2px; background: #FFFFFF; }
.labels { display: flex; justify-content: space-between; font-size: 13px; color: var(--stage-muted); }
.foot { display: flex; align-items: center; gap: 24px; font-size: 14px; color: var(--stage-muted); }
.dot { width: 4px; height: 4px; border-radius: 2px; background: #5A6F82; }
@media (max-width: 720px) {
  .stage { gap: 32px; padding: 0 16px; }
  .labels { font-size: 11px; }
  .foot { flex-direction: column; gap: 6px; text-align: center; }
  .dot { display: none; }
}
</style>
