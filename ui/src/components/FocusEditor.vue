<script setup lang="ts">
// Choose what to practise: worded by the owner, usually starting from a review item's words or a coach
// note's try line. Since 6 Oct it can carry a number (octo/measures.py), shown since the focus
// started against before; his answer after each conversation still decides.
import { nextTick, ref, watch } from 'vue'
import { MEASURE_LABEL } from '../lab/format'
import type { Measure } from '../lab/types'

const props = defineProps<{ open: boolean; initial: string; replaces: string | null; saving: boolean; error: string;
  /** The number chosen to start with, if any. */
  measure?: Measure | null }>()
const emit = defineEmits<{ save: [text: string, measure: Measure | null]; cancel: [] }>()
const MEASURES = Object.keys(MEASURE_LABEL) as Measure[]

const text = ref(props.initial)
const chosen = ref<Measure | ''>(props.measure ?? '')
const box = ref<HTMLTextAreaElement | null>(null)
const dlg = ref<HTMLDialogElement | null>(null)
watch(() => props.open, (o) => {
  if (o) {
    text.value = props.initial
    chosen.value = props.measure ?? ''
    dlg.value?.showModal()
    void nextTick(() => { box.value?.focus(); box.value?.select() })
  } else dlg.value?.close()
})
</script>

<template>
  <dialog ref="dlg" class="dlg" aria-labelledby="focus-h" @cancel.prevent="emit('cancel')">
    <form method="dialog" @submit.prevent="emit('save', text, chosen || null)">
      <h2 id="focus-h">Practise this</h2>
      <p class="muted small">Say it as something you'll do, in your words. Each conversation after today asks whether it came up.</p>
      <textarea ref="box" v-model="text" rows="3" maxlength="300" aria-label="What you'll practise" />
      <label class="pick">A number to watch with it
        <select v-model="chosen">
          <option value="">None: my answer after each conversation</option>
          <option v-for="m in MEASURES" :key="m" :value="m">{{ MEASURE_LABEL[m] }}</option>
        </select>
      </label>
      <p v-if="chosen" class="muted small">Shown since today against before. Your answer on each conversation still decides.</p>
      <p v-if="replaces" class="muted small">Replaces your current focus: “{{ replaces }}”</p>
      <p v-if="error" class="error small" role="alert">{{ error }}</p>
      <div class="actions">
        <button type="button" class="btn" @click="emit('cancel')">Cancel</button>
        <button type="submit" class="btn btn-primary" :disabled="saving || !text.trim()">{{ saving ? 'Saving…' : 'Make it my focus' }}</button>
      </div>
    </form>
  </dialog>
</template>

<style scoped>
.dlg { width: min(520px, calc(100vw - 32px)); border: 1px solid var(--line); border-radius: 14px; padding: 20px; background: var(--surface); color: var(--ink); }
.dlg::backdrop { background: rgba(0, 0, 0, 0.45); }
form { display: flex; flex-direction: column; gap: 10px; }
h2 { margin: 0; font-size: 18px; font-weight: 600; }
p { margin: 0; }
textarea { font: inherit; font-size: 15px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; resize: vertical; }
.actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
.pick { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--ink-2); }
select { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 10px;
  padding: 8px 10px; min-height: 44px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
</style>
