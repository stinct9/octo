<script setup lang="ts">
// Memos (docs/backlog.md B8, B9, B25): record a conversation on this phone, or Share → Octo from Voice Memos or a call
// recording in Notes. Each waits here until the Mac answers, then goes to the lab like any memo.
import { computed, onUnmounted, ref, watch } from 'vue'
import type { Lab } from '../lab'
import type { Memos } from '../memos'
import HoldButton from '../components/HoldButton.vue'
import MarkButton from '../components/MarkButton.vue'
import Sheet from '../components/Sheet.vue'

const props = defineProps<{ memos: Memos; lab: Lab }>()
const s = props.memos.s
const cue = s.cue
const ls = props.lab.s

// The recording's clock: a tick a second only while recording.
const now = ref(Date.now())
let tick: ReturnType<typeof setInterval> | undefined
watch(() => s.recording, (on) => {
  clearInterval(tick)
  if (on) { now.value = Date.now(); tick = setInterval(() => { now.value = Date.now() }, 1000) }
}, { immediate: true })
onUnmounted(() => clearInterval(tick))

const clock = computed(() => {
  const t = Math.max(0, Math.floor((now.value - s.startedAt) / 1000))
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), sec = t % 60
  const mm = String(m).padStart(h ? 2 : 1, '0'), ss = String(sec).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
})

const size = (b: number) => (b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} KB`)

const where = computed(() => {
  if (!ls.cfg) return 'Connect this phone to your Mac in the Lab tab, and these go to it.'
  if (s.sending) return `Sending to ${ls.cfg.mac}…`
  if (ls.phase === 'ready') return ''
  return `They go to ${ls.cfg.mac} as soon as it answers: at home, or anywhere with Tailscale.`
})

// Stop and send is a hold, as on the Recorder tab (Jay, 22 Oct): a tap only asks, so a stray tap can't end the memo.
const confirmStop = ref(false)
function stop() { confirmStop.value = false; void props.memos.stop() }

function remove(id: string, name: string) {
  if (confirm(`Delete "${name}" from this phone? Your Mac doesn't have it yet.`)) void props.memos.remove(id)
}
</script>

<template>
  <div class="memos">
    <h1 class="title">Memos</h1>

    <section class="record" :class="{ on: s.recording }">
      <template v-if="s.recording">
        <p class="clock" aria-live="off"><span class="dot" aria-hidden="true" />{{ clock }}</p>
        <p class="sub">Recording on this phone. It carries on with the screen locked.</p>
        <!-- B21: the live filler cue, as the recorder's screen: the filler big for 1.5 s, then the count -->
        <p v-if="cue.on && cue.available && cue.phrases.length && !cue.failure" class="cue" :class="{ flash: cue.flash }"
           aria-live="polite">{{ cue.flash || `Fillers · ${cue.count}` }}</p>
        <p v-if="cue.failure" class="error small" role="alert">{{ cue.failure }} The memo records as usual.</p>
        <MarkButton v-if="ls.cfg" :memos="memos" />
        <HoldButton recording stop="stop and send" @done="stop" @tap="confirmStop = true" />
      </template>
      <template v-else>
        <button type="button" class="btn primary" @click="memos.start()">Record on this phone</button>
        <p class="hint">Tell the people you're with that you're recording. It goes only to your Mac.</p>
      </template>
      <p v-if="s.failure" class="error small" role="alert">{{ s.failure }}</p>
    </section>

    <!-- B21: off until turned on, kept on this phone; the Mac's calls have their own switch (This Mac page) -->
    <section v-if="cue.available && !s.recording" class="cue-settings">
      <ul class="rows">
        <li><button type="button" class="row" role="switch" :aria-checked="cue.on" @click="memos.setCue(!cue.on)">
          <span>Show my fillers while recording</span><span class="value">{{ cue.on ? 'On' : 'Off' }}</span></button></li>
        <li v-if="cue.on"><button type="button" class="row" role="switch" :aria-checked="cue.haptic"
                @click="memos.setCue(true, !cue.haptic)"><span>Tap for each one</span>
          <span class="value">{{ cue.haptic ? 'On' : 'Off' }}</span></button></li>
      </ul>
      <p v-if="cue.on && !cue.phrases.length" class="hint">Connect to your Mac once to get your filler list.</p>
      <p v-else-if="cue.on" class="hint">Your list: {{ cue.phrases.join(', ') }}. iPhone's own speech recognition hears them on
        this phone; what it hears is only counted, never kept or sent. It hears more “uh” and “um” than the lab's transcript
        keeps.<template v-if="cue.haptic"> The tap may be heard faintly on the memo.</template> To change the list: Lab → Progress →
        Work on your fillers.</p>
    </section>

    <p class="hint">From Voice Memos: open a memo, tap Share, then <b>Octo</b>.</p>
    <p class="hint">A phone call: record it in the call (both of you hear iPhone say so). In Notes → Call Recordings, open
      it, tap the recording, then ⋯ → Share Audio → <b>Octo</b>.</p>

    <section v-if="s.items.length" class="waiting">
      <h2 class="label">Waiting to send · {{ s.items.length }}</h2>
      <ul class="rows">
        <li v-for="it in s.items" :key="it.id" class="row">
          <span class="what">
            <span class="name">{{ it.name }}</span>
            <span v-if="s.errors[it.id]" class="error small">{{ s.errors[it.id] }}</span>
            <span v-else class="hint">{{ size(it.size) }}</span>
          </span>
          <button type="button" class="link" :aria-label="`Delete ${it.name}`" @click="remove(it.id, it.name)">Delete</button>
        </li>
      </ul>
      <p v-if="where" class="hint" aria-live="polite">{{ where }}</p>
      <button v-if="ls.phase === 'ready' && !s.sending" type="button" class="btn" @click="memos.send()">Send now</button>
    </section>
    <p v-else-if="s.sent" class="ok small" aria-live="polite">Sent {{ s.sent }} to {{ ls.cfg?.mac ?? 'your Mac' }}. The lab takes it from there.</p>
    <p v-else-if="!ls.cfg" class="hint">{{ where }}</p>

    <Sheet :open="confirmStop" title="Stop and send?" @close="confirmStop = false">
      <p>The memo ends here and goes to your Mac.</p>
      <button type="button" class="btn danger" @click="stop">Stop and send</button>
      <button type="button" class="btn quiet" @click="confirmStop = false">Keep recording</button>
    </Sheet>
  </div>
</template>

<style scoped>
.memos { display: flex; flex-direction: column; gap: 16px; }
.record { display: flex; flex-direction: column; gap: 10px; padding: 16px; border-radius: 14px; background: var(--surface); border: 1px solid var(--line); }
.record.on { border-color: var(--rec); }
.clock { margin: 0; display: flex; align-items: center; gap: 10px; font-size: 2rem; font-weight: 600; font-variant-numeric: tabular-nums; }
.clock .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--rec); animation: blink 1.2s ease-in-out infinite; }
.cue { margin: 0; font-size: 1.1rem; color: var(--muted); font-variant-numeric: tabular-nums; }
.cue.flash { font-size: 2rem; font-weight: 700; color: var(--ink); }
.cue-settings { display: flex; flex-direction: column; gap: 8px; }
.waiting { display: flex; flex-direction: column; gap: 8px; }
.label { margin: 0; font-size: 0.8rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.what { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.name { overflow-wrap: anywhere; }
@media (prefers-reduced-motion: reduce) { .clock .dot { animation: none; } }
</style>
