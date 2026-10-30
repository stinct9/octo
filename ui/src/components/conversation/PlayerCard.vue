<script setup lang="ts">
// The player: the conversation's audio, who spoke when along the timeline, and where it's at. The page plays it.
import { ref } from 'vue'
import { lab } from '../../lab/api'
import { mss } from '../../lab/format'
import type { SessionDetail, Speaker } from '../../lab/types'

const props = defineProps<{
  s: SessionDetail
  t: number
  duration: number
  playing: boolean
  color: Record<string, string>
  who: (letter: string | null) => string
  nameOf: (sp: Speaker) => string
}>()
const emit = defineEmits<{ playing: [on: boolean]; time: [e: Event]; toggle: []; seek: [seconds: number] }>()

const audio = ref<HTMLAudioElement | null>(null)
defineExpose({ audio })
function seekTimeline(e: MouseEvent) {
  const box = (e.currentTarget as HTMLElement).getBoundingClientRect()
  emit('seek', ((e.clientX - box.left) / box.width) * props.duration)
}
</script>

<template>
  <section class="card player" aria-label="Player">
    <p v-if="s.audio_deleted" class="muted small">The audio was deleted after {{ s.audio_days ?? 60 }} days; the transcript and review stay.</p>
    <audio v-if="!s.audio_deleted" ref="audio" :src="lab.audioUrl(s.id)" preload="metadata"
           @play="emit('playing', true)" @pause="emit('playing', false)" @ended="emit('playing', false)"
           @timeupdate="emit('time', $event)" />
    <button type="button" class="play" :aria-label="playing ? 'Pause' : 'Play'" @click="emit('toggle')">
      <svg v-if="!playing" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor" /></svg>
      <svg v-else width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="2.5" width="3.5" height="11" rx="1" fill="currentColor" /><rect x="9.5" y="2.5" width="3.5" height="11" rx="1" fill="currentColor" /></svg>
    </button>
    <span class="mono time">{{ mss(t) }} / {{ mss(duration) }}</span>
    <div class="grow">
      <div class="timeline" role="slider" tabindex="0" aria-label="Position" :aria-valuenow="Math.round(t)"
           aria-valuemin="0" :aria-valuemax="Math.round(duration)" :aria-valuetext="mss(t)" @click="seekTimeline"
           @keydown.right.prevent="emit('seek', Math.min(duration, t + 5))" @keydown.left.prevent="emit('seek', Math.max(0, t - 5))">
        <span v-for="(turn, i) in s.turns" :key="i" class="seg" :class="color[turn.speaker]" :title="who(turn.speaker)"
              :style="{ left: `${(turn.start / duration) * 100}%`, width: `${Math.max(0.2, ((turn.end - turn.start) / duration) * 100)}%` }" />
        <span class="head-mark" :style="{ left: `${(t / (duration || 1)) * 100}%` }" />
      </div>
      <div class="legend muted small">
        <span v-for="sp in s.speakers" :key="sp.letter" class="key"><i :class="color[sp.letter]" />{{ nameOf(sp) }}</span>
        <span class="grow" />
        <span class="keys">Space plays · j / k next / previous line</span>
      </div>
    </div>
  </section>
</template>

<style scoped>
.grow { flex-grow: 1; min-width: 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
/* The player stays in reach while the review scrolls (M21), at every width since 6 Oct. */
.player { position: sticky; top: 0; z-index: 4; padding: 12px 16px; display: flex; align-items: center; gap: 14px; box-shadow: 0 6px 16px rgba(0, 0, 0, 0.06); }
.play { width: 44px; height: 44px; border-radius: 50%; border: 0; background: var(--accent); color: var(--accent-ink); display: flex; align-items: center; justify-content: center; cursor: pointer; flex-shrink: 0; }
.time { font-size: 13px; color: var(--muted); width: 100px; }
.timeline { position: relative; height: 20px; border-radius: 6px; background: var(--chip); cursor: pointer; overflow: hidden; }
.timeline .seg { position: absolute; top: 0; bottom: 0; }
.head-mark { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--ink); }
.legend { display: flex; gap: 14px; margin-top: 6px; flex-wrap: wrap; }
.key { display: flex; align-items: center; gap: 6px; }
.key i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
.seg, .key i { background: var(--c); }

/* No keyboard on the phone: no key hint. */
@media (hover: none) { .keys { display: none; } }
@media (max-width: 760px) {
  .legend { display: none; }
  .player { flex-wrap: wrap; padding: 10px 12px; }
}
</style>
