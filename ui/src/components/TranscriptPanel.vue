<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatValue, wer } from '../lib/format'
import type { Transcript } from '../types'

const props = defineProps<{ transcripts: Transcript[] }>()
const expected = ref('')

const en = computed(() => props.transcripts.find((t) => t.mode === 'en'))
const auto = computed(() => props.transcripts.find((t) => t.mode === 'auto'))
const score = computed(() => (en.value && expected.value.trim() ? wer(expected.value, en.value.text) : null))
const sameText = computed(() => en.value && auto.value && en.value.text === auto.value.text)
const LANG: Record<string, string> = { en: 'English', hi: 'Hindi', kn: 'Kannada', ta: 'Tamil', te: 'Telugu', mr: 'Marathi' }
</script>

<template>
  <section class="card panel" aria-label="Transcription">
    <div class="head">
      <h3>Transcription</h3>
      <span class="spacer" />
      <span class="mono muted small">local · faster-whisper</span>
    </div>

    <div v-if="en" class="block">
      <div class="eyebrow">Forced English</div>
      <p class="text">{{ en.text || '(nothing transcribed)' }}</p>
      <div class="muted small">confidence {{ formatValue(en.mean_logprob) }} log-prob · {{ en.seconds }} s</div>
    </div>

    <div v-if="auto" class="block">
      <div class="eyebrow">Auto-detect</div>
      <p v-if="!sameText" class="text secondary">{{ auto.text }}</p>
      <div class="muted small">
        <template v-if="sameText">Same text · </template>detected {{ LANG[auto.language] ?? auto.language }}, p = {{ auto.language_p.toFixed(2) }} · {{ auto.seconds }} s
      </div>
    </div>

    <div class="block">
      <label for="expected" class="eyebrow">What you said (optional, for word error rate)</label>
      <input id="expected" v-model="expected" type="text" placeholder="Type it to score the transcript" autocomplete="off" />
      <div v-if="score" class="small" :class="score.rate === 0 ? 'pass' : score.rate <= 0.1 ? 'warn' : 'fail'">
        {{ score.errors }} of {{ score.words }} words wrong · {{ Math.round(score.rate * 100) }}% word error rate
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel { border-radius: 16px; padding: 20px 22px; display: flex; flex-direction: column; gap: 18px; min-width: 0; }
.head { display: flex; align-items: baseline; gap: 10px; }
h3 { margin: 0; font-size: 18px; font-weight: 600; }
.spacer { flex-grow: 1; }
.block { display: flex; flex-direction: column; gap: 6px; padding-top: 16px; border-top: 1px solid var(--line); }
.head + .block { border-top: 0; padding-top: 0; }
.text { margin: 0; font-size: 20px; line-height: 1.5; }
.text.secondary { font-size: 16px; color: var(--ink-2); }
.muted { color: var(--muted); }
.small { font-size: 13px; }
input {
  height: 44px; padding: 0 12px; border: 1px solid var(--line-strong); border-radius: 10px;
  font: inherit; background: var(--surface); color: var(--ink);
}
</style>
