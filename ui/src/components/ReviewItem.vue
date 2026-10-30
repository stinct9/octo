<script setup lang="ts">
// One thing the full review says, headline first: it opens to the text, the quotes to hear, and
// the optional answers (Not right?, I acted on this, Practise this). Nothing here is required:
// since 6 Oct the conversation counts once the one question is answered (NewToYou).
import { computed } from 'vue'
import type { FeedbackChange } from '../lab/api'
import { mss } from '../lab/format'
import type { Held, ItemFeedback, Quote } from '../lab/types'
import HeldMark from './HeldMark.vue'
import NotRight from './NotRight.vue'

const props = defineProps<{
  item: string
  eyebrow: string
  title: string
  body?: string
  quotes?: Quote[]
  held?: Held | null
  feedback?: ItemFeedback
  open: boolean
  nameOf: (letter: string | null) => string
  color: Record<string, string>
  /** Offer "Practise this"; `focused` when it's already the focus. */
  practise?: boolean
  focused?: boolean
  /** Only when something about the evidence is worth saying (a quote dropped, a line unsure). */
  footnote?: string
  /** Answers given item by item before 6 Oct, shown as they were. */
  rated?: string
  /** The quote it's heard by from the headline row, without opening it (null: no audio). */
  moment?: Quote | null
  playing?: boolean
}>()
const emit = defineEmits<{ toggle: []; play: [seconds: number]; hear: []; practise: []; change: [change: FeedbackChange] }>()

const flagged = computed(() => props.feedback?.verdict === 'wrong' || props.feedback?.verdict === 'partly')
</script>

<template>
  <li :id="`item-${item}`" class="ri" :class="{ open }">
    <div class="head">
      <button type="button" class="toggle" :aria-expanded="open" :aria-controls="`body-${item}`" @click="emit('toggle')">
        <svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>
        <span class="titles">
          <span v-if="eyebrow" class="eyebrow-i">{{ eyebrow }}</span>
          <span class="title">{{ title }}</span>
        </span>
      </button>
      <span class="marks">
        <HeldMark v-if="held" :held="held" />
        <span v-if="focused" class="badge">Your focus</span>
        <span v-if="flagged" class="flag">{{ feedback?.verdict === 'wrong' ? 'Not right' : 'Partly right' }}</span>
        <span v-if="feedback?.acted" class="done">Acted on</span>
      </span>
      <button v-if="moment" type="button" class="hear" :class="{ on: playing }"
              :aria-label="`${playing ? 'Pause' : 'Hear it'}: ${nameOf(moment.speaker)} at ${mss(moment.start)}`"
              :title="`${nameOf(moment.speaker)}: “${moment.text}”`" @click="emit('hear')">
        <svg v-if="!playing" width="11" height="11" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5v11l9-5.5z" fill="currentColor" /></svg>
        <svg v-else width="10" height="10" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="2.5" width="3.5" height="11" rx="1" fill="currentColor" /><rect x="9.5" y="2.5" width="3.5" height="11" rx="1" fill="currentColor" /></svg>
      </button>
    </div>
    <div v-if="open" :id="`body-${item}`" class="body">
      <p v-if="body" class="text">{{ body }}</p>
      <button v-for="q in quotes ?? []" :key="`${q.line}-${q.text}`" type="button" class="quote" @click="emit('play', q.start)">
        <span class="mono small muted">▶ {{ mss(q.start) }}</span>
        <span><b :class="['who', color[q.speaker ?? '']]">{{ nameOf(q.speaker) }}</b> “{{ q.text }}”<span v-if="q.uncertain"
          class="warn" title="Unsure who said this, or some words"> ⚠</span></span>
      </button>
      <p v-if="footnote" class="small muted">{{ footnote }}</p>
      <p v-if="rated" class="small muted">Rated before 6 Oct: {{ rated }}</p>
      <div class="actions">
        <button v-if="practise && !focused" type="button" class="practise" @click="emit('practise')">Practise this</button>
        <NotRight :feedback="feedback" acted @change="emit('change', $event)" />
      </div>
    </div>
  </li>
</template>

<style scoped>
.ri { border-top: 1px solid var(--line); list-style: none; }
.ri:first-child { border-top: 0; }
.head { display: flex; align-items: flex-start; gap: 8px; padding: 4px 0; }
.toggle { flex: 1; min-width: 0; display: flex; align-items: flex-start; gap: 10px; text-align: left; border: 0; background: none; color: var(--ink);
  font: inherit; padding: 7px 6px; cursor: pointer; border-radius: 8px; min-height: 44px; }
.toggle:hover { background: var(--quote); }
.hear { width: 32px; height: 32px; margin-top: 6px; flex-shrink: 0; border-radius: 50%; border: 0; background: var(--accent-soft); color: var(--accent);
  display: flex; align-items: center; justify-content: center; cursor: pointer; }
.hear.on { background: var(--accent); color: var(--accent-ink); }
.chev { width: 16px; height: 16px; margin-top: 3px; flex-shrink: 0; fill: none; stroke: var(--ink-2); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; transition: transform 0.15s; }
.open .chev { transform: rotate(90deg); }
.titles { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.eyebrow-i { font-size: 12.5px; color: var(--muted); font-weight: 500; }
.title { font-weight: 500; line-height: 1.4; }
.marks { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; align-items: center; padding-top: 11px; }
.badge { font-size: 12px; font-weight: 500; color: var(--accent); background: var(--accent-soft); padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.flag { font-size: 12px; color: var(--ink-2); border: 1px dashed var(--line-strong); padding: 1px 8px; border-radius: 999px; white-space: nowrap; }
.done { font-size: 12px; color: var(--pass); white-space: nowrap; }
.body { padding: 0 6px 14px 32px; display: flex; flex-direction: column; gap: 8px; }
.text { margin: 0; color: var(--ink-2); }
.quote { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: 8px; text-align: left; border: 0; background: var(--quote);
  padding: 8px 10px; border-radius: 8px; cursor: pointer; color: var(--ink); font: inherit; }
.who { color: var(--c); }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; padding-top: 2px; }
.practise { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
  padding: 5px 12px; min-height: 34px; border-radius: 10px; cursor: pointer; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.warn { color: var(--warn); }
p { margin: 0; }
@media (max-width: 640px) {
  .body { padding-left: 8px; }
  .marks { order: 3; flex-basis: 100%; justify-content: flex-start; padding: 0 0 6px 32px; }
  .marks:empty { display: none; }
  .head { flex-wrap: wrap; }
  .hear { width: 44px; height: 44px; margin-top: 0; }
  .practise, .quote { min-height: 44px; }
}
</style>
