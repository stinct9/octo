<script setup lang="ts">
// The model reviews run on: the one place it's chosen (review 5 Nov, item 9), on Set up Octo and in Settings → Models
// and keys alike. The Hindi merge, the coach, Ask, Talk it through and Prepare follow it (settings.reader).
import { computed, onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import type { PipelineStep } from '../lab/types'

const emit = defineEmits<{ changed: [] }>()
const review = ref<PipelineStep | null>(null)
const pick = ref('')
const note = ref('')
const error = ref('')
// Usable choices, the owner's own connections first: having just connected one, that's what they mean.
const usable = computed(() => (review.value?.choices ?? []).filter((c) => !c.unavailable)
  .sort((a, b) => Number(!a.provider) - Number(!b.provider)))
const current = computed(() => review.value?.choices.find((c) => c.id === review.value?.selected))
const picked = computed(() => review.value?.choices.find((c) => c.id === pick.value))
const load = () => lab.pipeline().then((steps) => {
  review.value = steps.find((s) => s.stage === 'insight') ?? null
  pick.value = review.value?.selected ?? usable.value[0]?.id ?? ''
}).catch(() => {})
async function use() {
  error.value = ''
  try {
    const r = await lab.choose('insight', pick.value)
    review.value = r.steps.find((s) => s.stage === 'insight') ?? null
    note.value = `Reviews use ${review.value?.choices.find((c) => c.id === r.selected)?.label ?? r.selected}.`
    emit('changed')
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
onMounted(load)
defineExpose({ load, current })
</script>

<template>
  <div class="review-model">
    <div v-if="usable.length" class="line">
      <label class="field grow">Review with
        <select v-model="pick" aria-describedby="pick-why"><option v-for="c in usable" :key="c.id" :value="c.id">{{ c.label }}</option></select>
      </label>
      <button type="button" class="btn" :disabled="!pick || pick === current?.id" @click="use">Use it</button>
    </div>
    <p v-if="picked" id="pick-why" class="muted small">{{ picked.why }}</p>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <p v-if="note" class="ok small" aria-live="polite">{{ note }}</p>
  </div>
</template>

<style scoped>
.review-model { display: flex; flex-direction: column; gap: 6px; }
.review-model p { margin: 0; }
.line { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
.field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: var(--muted); }
.grow { flex: 1; min-width: 220px; }
select { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 44px; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
</style>
