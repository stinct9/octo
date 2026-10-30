<script setup lang="ts">
// The one question per conversation since 6 Oct (the owner, E2 in docs/coach-plan.md): "What here was new
// to you?" Tick the items, or say nothing was. Either answer counts the conversation as reviewed;
// the items ticked are what Progress counts as "something new". Answers can be changed later.
import { computed, ref, watch } from 'vue'
import type { Entry } from '../lab/review'
import type { Answer, ItemFeedback } from '../lab/types'

const props = defineProps<{
  entries: Entry[]
  answer: Answer | null
  feedback: Record<string, ItemFeedback>
  saving: boolean
  error: string
  /** The item that's the focus now, if it's in this review. */
  focusItem: string | null
  /** Reviewed before 6 Oct by rating every item: `answer` is what was marked new then. */
  legacy?: boolean
}>()
const emit = defineEmits<{ submit: [picked: string[], nothingNew: boolean]; practise: [item: string] }>()

// Ticked to start with: the answer given, or what was marked new item by item before 6 Oct.
const initial = () => new Set(props.answer ? props.answer.new
  : props.entries.filter((e) => props.feedback[e.id]?.novelty === 'new').map((e) => e.id))
const picked = ref<Set<string>>(initial())
const editing = ref(!props.answer)
// A new or changed answer (by content: the one shown for a review rated before 6 Oct is worked out
// afresh on every refresh, and a refresh mustn't close the list while it's being changed).
watch(() => JSON.stringify(props.answer), () => { picked.value = initial(); if (props.answer) editing.value = false })

function toggle(id: string, on: boolean) {
  const s = new Set(picked.value)
  if (on) s.add(id)
  else s.delete(id)
  picked.value = s
}
function done() { if (picked.value.size) emit('submit', props.entries.filter((e) => picked.value.has(e.id)).map((e) => e.id), false) }
function nothing() { emit('submit', [], true) }
function change() { picked.value = initial(); editing.value = true }
function cancel() { picked.value = initial(); editing.value = false }

const chosen = computed(() => props.entries.filter((e) => props.answer?.new.includes(e.id)))
const said = computed(() => {
  const a = props.answer
  if (!a) return ''
  const what = a.nothing_new ? (props.legacy ? 'nothing marked new' : 'nothing new this time') : `${a.new.length} new to you`
  return props.legacy ? `Reviewed before 6 Oct, item by item · ${what}` : `Reviewed · ${what}`
})
// Offer to practise one of them, unless one already is the focus.
const offer = computed(() => (chosen.value.some((e) => e.id === props.focusItem) ? [] : chosen.value.slice(0, 3)))
const short = (t: string) => (t.length > 48 ? `${t.slice(0, 46).trimEnd()}…` : t)
</script>

<template>
  <section class="q card" :class="{ waiting: !answer }" aria-labelledby="q-h">
    <template v-if="editing">
      <div class="head">
        <h2 id="q-h">What here was new to you?</h2>
        <span class="muted small">Answering counts this conversation as reviewed</span>
      </div>
      <p class="muted small">Tick what you wouldn't have seen yourself. One is plenty.</p>
      <ul class="opts">
        <li v-for="e in entries" :key="e.id">
          <label class="opt" :class="{ on: picked.has(e.id) }">
            <input type="checkbox" :checked="picked.has(e.id)" @change="toggle(e.id, ($event.target as HTMLInputElement).checked)">
            <span class="txt"><span class="eb">{{ e.eyebrow }}</span> {{ e.title }}</span>
          </label>
        </li>
      </ul>
      <p v-if="error" class="error small" role="alert">{{ error }}</p>
      <div class="actions">
        <button type="button" class="btn btn-primary" :disabled="!picked.size || saving" @click="done">
          {{ saving ? 'Saving…' : picked.size ? `Done · ${picked.size} new to me` : 'Done' }}</button>
        <button type="button" class="btn" :disabled="picked.size > 0 || saving"
                :title="picked.size ? 'Untick the items first' : undefined" @click="nothing">Nothing new this time</button>
        <button v-if="answer" type="button" class="link" @click="cancel">Cancel</button>
      </div>
    </template>
    <template v-else-if="answer">
      <div class="head">
        <h2 id="q-h" class="answered"><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8.5 3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
          {{ said }}</h2>
        <button type="button" class="link" aria-label="Change what was new to you" @click="change">Change</button>
      </div>
      <ul v-if="chosen.length" class="chosen">
        <li v-for="e in chosen" :key="e.id"><span class="eb">{{ e.eyebrow }}</span> · {{ e.title }}</li>
      </ul>
      <div v-if="offer.length" class="offer">
        <span class="muted small">Practise one of them?</span>
        <button v-for="e in offer" :key="e.id" type="button" class="practise" @click="emit('practise', e.id)">{{ short(e.title) }}</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
.q { padding: 18px 20px; display: flex; flex-direction: column; gap: 10px; }
.q.waiting { border: 1.5px solid var(--accent); }
.head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
.answered { display: flex; align-items: center; gap: 8px; color: var(--pass); font-weight: 500; font-size: 16px; }
.opts { list-style: none; margin: 0; padding: 0; }
.opts li + li { border-top: 1px solid var(--line); }
.opt { display: flex; align-items: flex-start; gap: 12px; padding: 9px 8px; cursor: pointer; min-height: 44px; border-radius: 8px; line-height: 1.45; }
.opt:hover { background: var(--quote); }
.opt.on { background: var(--accent-soft); }
.opt input { width: 18px; height: 18px; margin-top: 2px; accent-color: var(--accent); flex-shrink: 0; }
.txt { min-width: 0; }
.eb { font-size: 13px; color: var(--muted); font-weight: 500; margin-right: 4px; }
.actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding-top: 4px; }
.chosen { margin: 0; padding: 0 0 0 24px; display: flex; flex-direction: column; gap: 4px; color: var(--ink-2); }
.chosen .eb { font-size: inherit; margin-right: 0; }
.offer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.practise { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
  padding: 5px 12px; min-height: 34px; border-radius: 10px; cursor: pointer; max-width: 100%; text-align: left; }
.link { border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; min-height: 36px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); margin: 0; }
p { margin: 0; }
@media (max-width: 640px) {
  .actions .btn { flex: 1 1 100%; justify-content: center; }
  .practise { min-height: 44px; }
}
</style>
