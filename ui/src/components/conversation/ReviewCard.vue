<script setup lang="ts">
// The review card, read first: what happened (beside the owner's own words once both exist), what they wanted to do
// with these people, how they showed up, the one thing, and what to do. Until there's a review, why not yet.
import { computed } from 'vue'
import HowYouShowedUp from '../HowYouShowedUp.vue'
import OneThing from '../OneThing.vue'
import OwnerWords from '../OwnerWords.vue'
import TaskList from '../TaskList.vue'
import type { FeedbackChange } from '../../lab/api'
import { etaText } from '../../lab/format'
import { HAPPENED, happenedText } from '../../lab/intention'
import { href } from '../../lab/router'
import type { DeliveryMeasure, Insights, Quote, SessionDetail } from '../../lab/types'

const props = defineProps<{
  s: SessionDetail
  ins: Insights | null
  settled: boolean
  showSpeakers: boolean
  reflectBeside: boolean
  saveReflection: (text: string) => Promise<void>
  canHear: boolean
  color: Record<string, string>
  who: (letter: string | null) => string
  focusItem: string | null
  playingClip: string | null
  sending: string | null
  ttDone: Record<string, boolean | null>
  highlight: string | null
  ttError: string
  changeError: string
}>()
const markFillers = defineModel<boolean>('markFillers', { required: true })
const emit = defineEmits<{
  confirm: []
  play: [item: string, start: number, end: number]
  playQuote: [q: Quote]
  focus: [m: DeliveryMeasure]
  changed: []
  practise: [item: string]
  step: []
  change: [item: string, c: FeedbackChange]
  ticktick: [item: string]
  ticktickAll: []
}>()

const isReview = computed(() => props.ins?.kind === 'review')
const one = computed(() => props.ins?.one_thing ?? null)
const oneStep = computed(() => (one.value?.next != null ? props.ins?.next?.[one.value.next] ?? null : null))

// What it's doing now, beside when it'll be ready (B3).
const STAGE_NOW: Record<string, string> = { transcribe: 'Transcribing now.', speakers: 'Separating the voices now.',
  diarize: 'Matching words to voices now.', identify: 'Recognising voices now.', numbers: 'Counting now.', insight: 'Reviewing now.' }

// B15: what they wanted to do with these people, with what the review said of it.
const intentionRows = computed(() => {
  const said = props.ins?.intention
  if (said?.length) return said.map((i) => ({ person: i.person, text: i.text, happened: i.happened, observation: i.observation, quote: i.quotes[0] ?? null }))
  return (props.s.intentions ?? []).map((i) => ({ person: i.name, text: i.text, happened: null, observation: '', quote: null }))
})
</script>

<template>
  <section class="card review" aria-label="Review">
    <p v-if="!settled" class="muted">The review runs once speakers are confirmed, so nothing is said about the wrong person.
      <button v-if="!showSpeakers" type="button" class="link" @click="emit('confirm')">Confirm them</button></p>
    <p v-else-if="!s.insights && s.todo.kind === 'not_you'" class="muted">Without you · numbers only. Nobody here is
      confirmed as you, so there's no review: it's about your conduct. If one of them is you, name that speaker.</p>
    <p v-else-if="!s.insights && s.todo.kind === 'failed'" class="muted">No review yet: see above.</p>
    <p v-else-if="!s.insights && s.todo.kind === 'processing' && (s.todo.eta || s.todo.late)" class="muted">{{
      s.todo.then === 'review' ? 'The review' : 'Ready for you' }} {{ etaText(s.todo.eta, s.todo.late) }}<template
      v-if="s.todo.detail"> · {{ s.todo.detail }}</template>. {{ s.todo.queued ? 'Waiting its turn.' : STAGE_NOW[s.todo.stage] ?? '' }}<template
      v-if="s.todo.then !== 'review'"> Then, if anyone needs naming, you confirm who's who; the review takes about a minute after.</template></p>
    <p v-else-if="!s.insights && s.todo.kind === 'processing' && s.todo.queued" class="muted">Waiting its turn{{
      s.todo.detail ? `: ${s.todo.detail}` : '' }}. The review itself takes a minute or two.</p>
    <p v-else-if="!s.insights && s.todo.kind === 'part'" class="muted">This is a part of one discussion: its review reads
      every part, <a :href="href.conversation(s.todo.head)">on the whole</a>.</p>
    <p v-else-if="!s.insights" class="muted">Reading the conversation… this takes a minute or two.</p>
    <p v-else-if="s.insights.skipped" class="muted">{{ s.insights.skipped }}.</p>
    <template v-else-if="isReview">
      <div class="summary" :class="{ beside: reflectBeside }">
        <div class="said">
          <div class="eyebrow-s">What happened</div>
          <p class="lead">{{ s.insights.summary }}</p>
        </div>
        <OwnerWords v-if="reflectBeside" class="yours" label="In your words" :text="s.reflection?.text ?? null" :save="saveReflection"
                    multiline :max="4000" />
      </div>
      <div v-if="intentionRows.length" class="block intents">
        <div class="eyebrow-s">What you wanted to do</div>
        <div v-for="i in intentionRows" :key="i.person" class="intent">
          <p>Next time with {{ i.person }}, I want to: “{{ i.text }}”</p>
          <p class="small"><b :class="i.happened ? HAPPENED[i.happened].tone : 'muted'">{{ happenedText(i.happened, !!ins?.intention) }}</b><template
            v-if="i.observation"> · {{ i.observation }}</template></p>
          <p v-if="i.quote" class="small q">“{{ i.quote.text }}” <button v-if="canHear" type="button" class="link" @click="emit('playQuote', i.quote)">Hear it</button></p>
        </div>
      </div>
      <HowYouShowedUp v-if="s.delivery" v-model:mark-fillers="markFillers" :d="s.delivery" :can-hear="canHear"
                      :cues="s.recorder_cues?.length ?? null" :playing="playingClip" @play="(...a) => emit('play', ...a)" @focus="emit('focus', $event)" @changed="emit('changed')" />
      <OneThing v-if="one" :one="one" :step="oneStep" :name-of="who" :color="color" :focused="focusItem === 'o'"
                :playing="playingClip" @play="emit('playQuote', $event)" @practise="emit('practise', 'o')" @step="emit('step')" />
      <div v-if="s.insights.next?.length" class="block">
        <TaskList :tasks="s.insights.next" :feedback="s.feedback" :sent="s.ticktick" :connected="s.ticktick_connected"
                  :sending="sending" :settings-href="href.settings" :tt-done="ttDone" :focus-item="focusItem"
                  :one-step="one?.next ?? null" :highlight="highlight"
                  @change="(item, c) => emit('change', item, c)" @practise="emit('practise', $event)" @ticktick="emit('ticktick', $event)" @ticktick-all="emit('ticktickAll')" />
        <p v-if="ttError" class="error small" role="alert">{{ ttError }}</p>
      </div>
    </template>
    <p v-else class="muted">A local review, one pass per principle: below.</p>
    <HowYouShowedUp v-if="s.delivery && settled && !isReview" v-model:mark-fillers="markFillers" :d="s.delivery" :can-hear="canHear"
                    :cues="s.recorder_cues?.length ?? null" :playing="playingClip" @play="(...a) => emit('play', ...a)" @focus="emit('focus', $event)" @changed="emit('changed')" />
    <p v-if="changeError" class="error small" role="alert">{{ changeError }}</p>
  </section>
</template>

<style scoped>
.muted { color: var(--muted); }
.small { font-size: 13px; }
.warn { color: var(--warn); }
.error { color: var(--fail); margin: 0; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
/* TaskList's own root */
.head { display: flex; align-items: center; gap: 12px; }

.review { padding: 20px 22px; display: flex; flex-direction: column; gap: 22px; }
.review > p { margin: 0; }
.summary { display: flex; flex-direction: column; gap: 4px; }
.summary.beside { display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 20px; align-items: start; }
.summary .said { display: flex; flex-direction: column; gap: 4px; }
.yours { border-left: 2px solid var(--line); padding-left: 14px; }
.intent { display: flex; flex-direction: column; gap: 2px; }
.intent p { margin: 0; }
.intent .q { color: var(--ink-2); }
.intent .done { color: var(--pass); }
.intent .warn { color: var(--warn); }
@media (max-width: 640px) { .summary.beside { grid-template-columns: 1fr; } .yours { border-left: 0; padding-left: 0; } }
.eyebrow-s { font-size: 13px; color: var(--muted); font-weight: 600; }
.lead { margin: 0; color: var(--ink); font-size: 16px; line-height: 1.55; }
.block { display: flex; flex-direction: column; gap: 8px; }

@media (max-width: 760px) {
  .head { flex-wrap: wrap; }
  .review { padding: 16px; }
  .link { min-height: 44px; }
  p .link { min-height: 0; padding: 12px 0; margin: -12px 0; }  /* in a line of text: the tap area grows, the line doesn't */
}
</style>
