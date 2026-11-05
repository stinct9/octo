<script setup lang="ts">
// B38, goal 2: who has solved this, in the owner's words, on the focus or a coach note's "work on". Octo never
// suggests a person, a book or a quote; the coach and Talk it through read only what's written here.
import { ref, watch } from 'vue'
import type { Solved, SolvedInput } from '../lab/types'

const props = defineProps<{ value: Solved | null | undefined; save: (s: SolvedInput) => Promise<unknown> }>()

const editing = ref(false)
const who = ref('')
const what = ref('')
const source = ref('')
const saving = ref(false)
const error = ref('')
function reset() {
  who.value = props.value?.who ?? ''
  what.value = props.value?.what ?? ''
  source.value = props.value?.source ?? ''
}
watch(() => props.value, reset, { immediate: true })

async function send(s: SolvedInput) {
  saving.value = true
  try { await props.save(s); editing.value = false; error.value = '' } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { saving.value = false }
}
</script>

<template>
  <details class="solved">
    <summary>Who has solved this?<span v-if="value" class="muted"> · {{ value.who }}</span></summary>
    <div v-if="value && !editing" class="body">
      <p><b>{{ value.who }}</b><template v-if="value.source"> · <i>{{ value.source }}</i></template></p>
      <p class="what">{{ value.what }}</p>
      <p class="muted small">Your words. The coach and Talk it through read only this, as your account of them.</p>
      <div class="acts">
        <button type="button" class="link small" @click="editing = true">Edit</button>
        <button type="button" class="link small" :disabled="saving" @click="send({ who: '', what: '', source: null })">Remove</button>
      </div>
    </div>
    <form v-else class="body" @submit.prevent="send({ who, what, source: source.trim() || null })">
      <p class="muted small">Someone you know, an author or a speaker who has been here. In your words: Octo looks nothing up
        and suggests no one. The coach and Talk it through can then ask what they'd do, only from what you write.</p>
      <label>Who<input v-model="who" maxlength="120" placeholder="A person, a book's author, a speaker" /></label>
      <label>What they did, or would say<textarea v-model="what" rows="3" maxlength="1000" /></label>
      <label>Where from <span class="muted">(optional: a book or a talk)</span><input v-model="source" maxlength="200" /></label>
      <p v-if="error" class="error small" role="alert">{{ error }}</p>
      <div class="acts">
        <button v-if="value" type="button" class="btn" @click="editing = false; reset()">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="saving || !who.trim() || !what.trim()">{{ saving ? 'Saving…' : 'Keep it' }}</button>
      </div>
    </form>
  </details>
</template>

<style scoped>
.solved { flex-basis: 100%; font-size: 14px; }
summary { cursor: pointer; color: var(--accent); font-size: 13px; min-height: 32px; display: flex; align-items: center; }
.body { display: flex; flex-direction: column; gap: 8px; padding: 6px 0 2px; }
p { margin: 0; }
.what { white-space: pre-wrap; }
label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--ink-2); }
input, textarea { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line);
  border-radius: 10px; padding: 8px 10px; min-height: 40px; }
textarea { resize: vertical; }
.acts { display: flex; gap: 10px; justify-content: flex-end; align-items: center; }
.link { border: 0; background: none; padding: 8px 4px; color: var(--accent); cursor: pointer; font: inherit; min-height: 40px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
</style>
