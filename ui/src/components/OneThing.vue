<script setup lang="ts">
// The one thing from this conversation (reviews since 6 Oct): the headline a perceptive colleague
// would give first, why, its moment to hear, and the step that acts on it.
import { mss } from '../lab/format'
import type { OneThing, Quote, Task } from '../lab/types'

defineProps<{
  one: OneThing
  step: Task | null
  nameOf: (letter: string | null) => string
  color: Record<string, string>
  focused: boolean
  /** The quote playing now, if one of these is. */
  playing: string | null
}>()
const emit = defineEmits<{ play: [q: Quote]; practise: []; step: [] }>()
const key = (q: Quote) => `o-${q.line}`
</script>

<template>
  <section class="one" aria-labelledby="one-h">
    <div class="eyebrow-o">The one thing<span v-if="focused" class="badge">Your focus</span></div>
    <h2 id="one-h">{{ one.headline }}</h2>
    <p v-if="one.why" class="why">{{ one.why }}</p>
    <ul class="moments">
      <li v-for="q in one.quotes" :key="key(q)">
        <button type="button" class="moment" :aria-pressed="playing === key(q)" @click="emit('play', q)">
          <span class="pm" aria-hidden="true">{{ playing === key(q) ? '❚❚' : '▶' }}</span>
          <span class="grow"><b :class="['who', color[q.speaker ?? '']]">{{ nameOf(q.speaker) }}</b> “{{ q.text }}”<span
            v-if="q.uncertain" class="warn" title="Unsure who said this, or some words"> ⚠</span></span>
          <span class="mono muted small nowrap">{{ mss(q.start) }}</span>
        </button>
      </li>
    </ul>
    <div class="foot">
      <button v-if="step" type="button" class="link step" @click="emit('step')">Step: {{ step.title ?? step.action }} ↓</button>
      <span class="grow" />
      <button v-if="!focused" type="button" class="practise" @click="emit('practise')">Practise this</button>
    </div>
  </section>
</template>

<style scoped>
.one { border-left: 3px solid var(--accent); padding: 4px 0 4px 16px; display: flex; flex-direction: column; gap: 8px; }
.eyebrow-o { font-size: 13px; color: var(--accent); font-weight: 600; display: flex; align-items: center; gap: 10px; }
h2 { margin: 0; font-size: 20px; font-weight: 600; line-height: 1.35; }
.why { margin: 0; color: var(--ink-2); }
.moments { list-style: none; margin: 2px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.moment { width: 100%; display: flex; align-items: center; gap: 12px; text-align: left; border: 1px solid var(--line); background: var(--surface);
  color: var(--ink); font: inherit; padding: 8px 12px; min-height: 44px; border-radius: 10px; cursor: pointer; }
.moment:hover, .moment[aria-pressed='true'] { background: var(--quote); }
.pm { width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
  background: var(--accent-soft); color: var(--accent); font-size: 12px; }
.grow { flex: 1; min-width: 0; }
.who { color: var(--c); }
.foot { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.link { border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; text-align: left; min-height: 36px; }
.step { font-size: 14px; }
.practise { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
  padding: 5px 12px; min-height: 36px; border-radius: 10px; cursor: pointer; }
.badge { font-size: 12px; font-weight: 500; color: var(--accent); background: var(--accent-soft); padding: 2px 9px; border-radius: 999px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.warn { color: var(--warn); }
.nowrap { white-space: nowrap; }
@media (max-width: 640px) { h2 { font-size: 18px; } .practise, .link { min-height: 44px; } }
</style>
