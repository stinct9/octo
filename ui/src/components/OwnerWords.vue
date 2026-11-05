<script setup lang="ts">
// B15: a few of the owner's own words, saved when they say so: an intention (one line) or a reflection (a box,
// where the phone's dictation gives voice). Shown as written once saved, with Change; empty clears it.
import { nextTick, onMounted, ref } from 'vue'

const props = defineProps<{ label: string; text: string | null; save: (text: string) => Promise<unknown>; multiline?: boolean
  placeholder?: string; max: number
  /** Opened by a tap (a folded line, review 5 Nov item 4): the box takes the focus. */
  autofocus?: boolean }>()
const editing = ref(!props.text)
const draft = ref(props.text ?? '')
const saving = ref(false)
const error = ref('')
const box = ref<HTMLInputElement | HTMLTextAreaElement | null>(null)
onMounted(() => { if (props.autofocus && editing.value) box.value?.focus() })

async function edit() {
  draft.value = props.text ?? ''
  editing.value = true
  await nextTick()
  box.value?.focus()
  box.value?.select()
}
async function submit() {
  saving.value = true
  error.value = ''
  try {
    await props.save(draft.value.trim())
    editing.value = !draft.value.trim()
  } catch (e) {
    error.value = `Couldn't save that: ${e instanceof Error ? e.message : e}`
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="ow">
    <form v-if="editing" @submit.prevent="submit">
      <label class="lbl">{{ label }}
        <textarea v-if="multiline" ref="box" v-model="draft" rows="3" :maxlength="max" :placeholder="placeholder" />
        <input v-else ref="box" v-model="draft" :maxlength="max" :placeholder="placeholder" enterkeyhint="done" />
      </label>
      <div class="acts">
        <button type="submit" class="btn btn-primary" :disabled="saving || (!draft.trim() && !text)">{{ saving ? 'Saving…' : draft.trim() || !text ? 'Save' : 'Clear it' }}</button>
        <button v-if="text" type="button" class="btn" @click="editing = false">Cancel</button>
      </div>
    </form>
    <div v-else class="said">
      <span class="lbl">{{ label }}</span>
      <p class="words">{{ text }}</p>
      <button type="button" class="link" @click="edit">Change</button>
    </div>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.ow { display: flex; flex-direction: column; gap: 6px; }
form, .lbl { display: flex; flex-direction: column; gap: 6px; }
.lbl { font-size: 13px; color: var(--ink-2); font-weight: 500; }
input, textarea { font: inherit; font-size: 15px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 10px;
  padding: 9px 12px; min-height: 40px; width: 100%; box-sizing: border-box; }
textarea { resize: vertical; }
.acts { display: flex; gap: 8px; }
.said { display: flex; flex-direction: column; gap: 2px; align-items: flex-start; }
.words { margin: 0; white-space: pre-wrap; color: var(--ink); }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; font-size: 13px; min-height: 32px; }
.small { font-size: 13px; }
.error { color: var(--fail); margin: 0; }
</style>
