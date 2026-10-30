<script setup lang="ts">
// The optional answers on one review item since 6 Oct: "Not right?" (wrong, or partly right, with a
// note) and "I acted on this". Neither is needed for the conversation to count as reviewed; "Not
// right" keeps the trust signal, and the coach leaves out what the owner marked wrong.
import { computed, ref, watch } from 'vue'
import type { FeedbackChange } from '../lab/api'
import type { ItemFeedback } from '../lab/types'

const props = defineProps<{ feedback?: ItemFeedback; acted?: boolean }>()
const emit = defineEmits<{ change: [change: FeedbackChange] }>()

const fb = computed(() => props.feedback)
const flagged = computed(() => fb.value?.verdict === 'wrong' || fb.value?.verdict === 'partly')
const asking = ref(false)
const note = ref(fb.value?.note ?? '')
watch(() => props.feedback?.note, (n) => { note.value = n ?? '' })
const uid = Math.random().toString(36).slice(2, 8)

function choose(v: 'wrong' | 'partly') {
  asking.value = false
  emit('change', { verdict: v })
}
function clear() {
  emit('change', { verdict: '' })
}
function saveNote() {
  if ((fb.value?.note ?? '') !== note.value.trim()) emit('change', { note: note.value.trim() })
}
</script>

<template>
  <div class="nr">
    <template v-if="flagged">
      <span class="said">You said: {{ fb?.verdict === 'wrong' ? 'not right' : 'partly right' }}</span>
      <button type="button" class="link" aria-label="Change whether it's right" @click="asking = !asking">Change</button>
      <button type="button" class="link" aria-label="Clear: it's not marked wrong" @click="clear">Clear</button>
    </template>
    <button v-else type="button" class="link" :aria-expanded="asking" :aria-controls="`nr-${uid}`" @click="asking = !asking">Not right?</button>
    <label v-if="acted" class="check"><input type="checkbox" :checked="fb?.acted" @change="emit('change', { acted: ($event.target as HTMLInputElement).checked })"> I acted on this</label>
    <div v-if="asking" :id="`nr-${uid}`" class="ask" role="group" aria-label="Is it wrong, or partly right?">
      <button type="button" class="opt" :aria-pressed="fb?.verdict === 'wrong'" @click="choose('wrong')">Wrong</button>
      <button type="button" class="opt" :aria-pressed="fb?.verdict === 'partly'" @click="choose('partly')">Partly right</button>
      <button type="button" class="link" @click="asking = false">Cancel</button>
    </div>
    <label v-if="flagged || fb?.acted || fb?.note" class="field">{{ fb?.acted && !flagged ? 'What you did' : 'What it missed' }} (optional)
      <input v-model="note" maxlength="1000" @blur="saveNote" @keydown.enter.prevent="saveNote">
    </label>
  </div>
</template>

<style scoped>
.nr { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; font-size: 13px; }
.link { border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; min-height: 32px; }
.said { color: var(--ink-2); font-weight: 500; }
.ask { flex-basis: 100%; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.opt { font: inherit; font-size: 13px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); padding: 5px 12px; min-height: 34px; border-radius: 9px; cursor: pointer; }
.opt[aria-pressed='true'] { border-color: var(--accent); background: var(--accent-soft); color: var(--accent); }
.check { display: flex; align-items: center; gap: 7px; min-height: 32px; color: var(--ink-2); }
.check input { width: 16px; height: 16px; accent-color: var(--accent); }
.field { flex-basis: 100%; display: flex; flex-direction: column; gap: 4px; color: var(--muted); }
.field input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 38px; }
@media (max-width: 640px) { .link, .opt, .check { min-height: 44px; } }
</style>
