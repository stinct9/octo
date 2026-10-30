<script setup lang="ts">
// Which filler the recorder's cue works on (B29, docs/filler-training-plan.md §3; on the Fillers page): the owner's fillers with how ready each is, a
// training job as it goes (one at a time, cancellable), and what each finished training found. Mac only.
import { computed, ref } from 'vue'
import { STEPS, alsoLine, leansLine, minutesLeft, resultLine, stateLine, type Target } from '../lab/filler'

const props = defineProps<{ targets: Target[]; job: { target: string; started_at: number } | null; installed: string
  /** rec-27 (B29): the recorder takes models as data; whether it's recording now (it refuses then) */
  takes: boolean; recording: boolean; installing: boolean }>()
const emit = defineEmits<{ changed: [] }>()

const busy = ref(false)
const error = ref('')
const asking = ref<string | null>(null)   // "Train anyway?" open for this filler
const cancelling = ref(false)

async function post(path: string, body: unknown) {
  busy.value = true
  error.value = ''
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail ?? res.statusText)
    asking.value = null
    cancelling.value = false
    emit('changed')
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = false
  }
}
const train = (target: string) => post('/api/octo/settings/recorder/filler/train', { target })
const cancel = () => post('/api/octo/settings/recorder/filler/train/cancel', {})
const install = (target: string) => post('/api/octo/settings/recorder/filler/install', { target })

const running = computed(() => (props.job ? props.targets.find((t) => t.target === props.job!.target)?.training ?? null : null))
const stepAt = computed(() => STEPS.findIndex((s) => s.step === running.value?.step))
const minutes = computed(() => (running.value ? minutesLeft(running.value, Date.now() / 1000) : null))
const sinceMin = computed(() => (props.job ? Math.max(1, Math.round((Date.now() / 1000 - props.job.started_at) / 60)) : 0))
</script>

<template>
  <div class="targets">
    <section v-if="job" class="job" aria-labelledby="job-h">
      <div class="row-head">
        <h4 id="job-h">Training “{{ job.target }}”</h4>
        <span v-if="minutes" class="muted small">About {{ minutes }} minutes left</span>
      </div>
      <ol class="steps" aria-live="polite">
        <li v-for="(s, i) in STEPS" :key="s.step" :class="{ done: i < stepAt, now: i === stepAt }">
          <span class="mark" aria-hidden="true">{{ i < stepAt ? '✓' : i === stepAt ? '●' : '○' }}</span>
          <span>{{ s.label }}
            <span v-if="s.step === 'gathering' && running?.examples != null" class="fix">{{ running.examples }} of yours from
              {{ running.conversations }} conversations</span>
            <span v-if="s.step === 'testing' && i === stepAt && running?.trainings" class="fix">{{ running.done ?? 0 }} of
              {{ running.trainings }}
              <progress :max="running.trainings" :value="running.done ?? 0" aria-label="Tests done">{{ running.done }}</progress></span>
          </span>
        </li>
      </ol>
      <p class="muted small">The Mac is busier meanwhile: new conversations may take longer to transcribe. You can leave this page.</p>
      <div v-if="!cancelling" class="line"><button type="button" class="btn btn-small" :disabled="busy" @click="cancelling = true">Cancel…</button></div>
      <div v-else class="line" role="group" aria-label="Cancel the training?">
        <span class="small">Cancelling loses the {{ sinceMin }} minute{{ sinceMin === 1 ? '' : 's' }} so far.</span>
        <button type="button" class="btn btn-small" :disabled="busy" @click="cancel">Cancel training</button>
        <button type="button" class="btn btn-small" :disabled="busy" @click="cancelling = false">Keep going</button>
      </div>
    </section>

    <ul class="list" aria-label="Fillers to train">
      <li v-for="t in targets" :key="t.target">
        <div class="what">
          <span class="name">{{ t.target }}</span>
          <span v-if="alsoLine(t)" class="muted small">{{ alsoLine(t) }}</span>
          <span v-if="leansLine(t)" class="muted small">{{ leansLine(t) }}</span>
          <span v-if="resultLine(t.target, t.training) && t.target !== installed" class="small result">{{ resultLine(t.target, t.training) }}
            <template v-if="t.training?.candidate && !takes"> It goes on the recorder once its firmware is rec-27 or later
              (`octo device flash`).</template></span>
          <span v-if="t.training?.candidate && takes && t.target !== installed" class="line">
            <button type="button" class="btn btn-small" :disabled="busy || recording || installing || !!job"
                    @click="install(t.target)">Put “{{ t.target }}” on the recorder</button>
            <span v-if="recording" class="muted small">Stop recording first: hold the recorder's button 1 s.</span>
            <span v-else class="muted small">In place of “{{ installed }}”, which stays on it to put back.</span>
          </span>
        </div>
        <div class="act">
          <span v-if="job?.target === t.target" class="small muted">Training…</span>
          <template v-else>
            <span class="small" :class="{ ok: t.status === 'on', muted: t.status !== 'on' }">{{ stateLine(t) }}</span>
            <button v-if="t.status === 'ready' && !job" type="button" class="btn btn-small" :disabled="busy"
                    @click="train(t.target)">{{ t.training?.caught != null ? 'Train again' : 'Train' }}</button>
            <button v-else-if="t.status === 'ordinary' && !job && asking !== t.target" type="button" class="link"
                    :aria-expanded="false" @click="asking = t.target">Train anyway…</button>
          </template>
        </div>
        <div v-if="asking === t.target" class="ask" role="group" :aria-label="`Train ${t.target} anyway?`">
          <span class="small">It will also flash when you use “{{ t.target }}” as an ordinary word, which is most of the time for
            you. Train anyway?</span>
          <button type="button" class="btn btn-small" :disabled="busy" @click="train(t.target)">Train anyway</button>
          <button type="button" class="btn btn-small" :disabled="busy" @click="asking = null">Not now</button>
        </div>
      </li>
    </ul>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.targets { display: flex; flex-direction: column; gap: 8px; }
h4 { margin: 6px 0 0; font-size: 14px; font-weight: 600; }
p { margin: 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.error { color: var(--fail); }
.row-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.line { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.job { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; }
.steps { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
.steps li { display: flex; gap: 10px; align-items: baseline; color: var(--muted); }
.steps li.done, .steps li.now { color: var(--ink); }
.steps .done .mark { color: var(--pass); }
.steps .now .mark { color: var(--accent); }
.mark { width: 16px; flex-shrink: 0; text-align: center; font-weight: 600; }
.fix { display: block; color: var(--muted); font-size: 13px; margin-top: 2px; }
progress { display: block; width: 100%; max-width: 280px; margin-top: 4px; accent-color: var(--accent); }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; }
.list li { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 6px 12px; padding: 8px 0;
  border-top: 1px solid var(--line); }
.list li:first-child { border-top: 0; }
.what { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1 1 260px; }
.name { font-weight: 600; font-size: 14px; }
.result { color: var(--ink); }
.act { display: flex; gap: 10px; align-items: center; justify-content: flex-end; flex-wrap: wrap; text-align: right; }
.ask { flex-basis: 100%; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.link { border: 0; background: none; padding: 6px 0; color: var(--accent); cursor: pointer; font: inherit; font-size: 13px; min-height: 32px; }
@media (max-width: 640px) {
  .act { justify-content: flex-start; text-align: left; }
  .link { min-height: 44px; }
}
</style>
