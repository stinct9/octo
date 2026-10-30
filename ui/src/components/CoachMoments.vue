<script setup lang="ts">
// The moments a coach theme rests on: each a review item from one conversation (when, with whom,
// what the review said) and its quote to hear, newest first. The first few show; the rest open.
import { computed, ref } from 'vue'
import { day } from '../lab/format'
import { href } from '../lab/router'
import type { CoachEvidence } from '../lab/types'
import HeldMark from './HeldMark.vue'

const props = defineProps<{ evidence: CoachEvidence[]; playing: string | null; show?: number }>()
const emit = defineEmits<{ play: [e: CoachEvidence] }>()
const all = ref(false)
const shown = computed(() => (all.value ? props.evidence : props.evidence.slice(0, props.show ?? 3)))
const rest = computed(() => props.evidence.length - shown.value.length)
const where = (e: CoachEvidence) => [day(e.at), e.with.length ? `with ${e.with.join(', ')}` : ''].filter(Boolean).join(' · ')
const canHear = (e: CoachEvidence) => e.audio && !!e.quote
</script>

<template>
  <ul class="moments">
    <li v-for="e in shown" :key="e.id">
      <component :is="canHear(e) ? 'button' : 'div'" :type="canHear(e) ? 'button' : undefined" class="moment"
                 :class="{ still: !canHear(e) }" :aria-pressed="canHear(e) ? playing === e.id : undefined"
                 :aria-label="canHear(e) ? `${playing === e.id ? 'Pause' : 'Hear it'}: ${where(e)}, ${e.label}` : undefined"
                 @click="canHear(e) && emit('play', e)">
        <span v-if="canHear(e)" class="pm" aria-hidden="true">{{ playing === e.id ? '❚❚' : '▶' }}</span>
        <span class="grow">
          <span class="meta">{{ where(e) }} · {{ e.label }}<HeldMark v-if="e.held" :held="e.held" bare class="hm" /></span>
          <span class="head">{{ e.headline }}</span>
          <span v-if="e.quote" class="q"><b :class="e.quote.mine ? 'me' : 'other'">{{ e.quote.mine ? 'You' : e.quote.who }}</b> “{{ e.quote.text }}”</span>
        </span>
      </component>
      <a class="open" :href="href.conversation(e.session_id)" :aria-label="`Open the conversation of ${where(e)}`">Open</a>
    </li>
  </ul>
  <button v-if="rest > 0 || all" type="button" class="more" @click="all = !all">{{ all ? 'Fewer moments' : `All ${evidence.length} moments` }}</button>
</template>

<style scoped>
.moments { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
li { display: flex; align-items: stretch; gap: 6px; }
.moment { flex: 1; min-width: 0; display: flex; align-items: flex-start; gap: 12px; text-align: left; border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); font: inherit; padding: 9px 12px; min-height: 44px; border-radius: 10px; cursor: pointer; }
.moment.still { cursor: default; padding-left: 14px; }
button.moment:hover, .moment[aria-pressed='true'] { background: var(--quote); }
.pm { width: 30px; height: 30px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center; justify-content: center;
  background: var(--accent-soft); color: var(--accent); font-size: 12px; margin-top: 2px; }
.grow { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.meta { font-size: 12.5px; color: var(--muted); display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.hm { font-size: 12px; }
.head { font-size: 14px; font-weight: 500; }
.q { font-size: 14px; color: var(--ink-2); }
.q b { font-weight: 600; }
.q b.me { color: var(--me); }
.q b.other { color: var(--ink); }
.open { display: flex; align-items: center; padding: 0 10px; border-radius: 10px; color: var(--accent); text-decoration: none; font-size: 13px;
  border: 1px solid transparent; }
.open:hover { border-color: var(--line); background: var(--surface); }
.more { align-self: flex-start; border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; font-size: 13px; min-height: 36px; }
@media (max-width: 640px) { .open { padding: 0 6px; } .more { min-height: 44px; } }
</style>
