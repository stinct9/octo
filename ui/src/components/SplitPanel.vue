<script setup lang="ts">
import { computed, ref } from 'vue'
import { lab } from '../lab/api'
import { mss } from '../lab/format'
import { href } from '../lab/router'
import { canCut, parts } from '../lab/split'
import type { Line, SplitSuggestion } from '../lab/types'

// Several discussions in one conversation (octo/split.py): cuts where the people change, or by hand at the playhead,
// as many as it needs, made in one go. The first part stays this conversation; each later one becomes a new one.
const props = defineProps<{
  id: string
  suggestions: SplitSuggestion[]
  lines: Line[]
  duration: number
  now: number
  audioDeleted: boolean
  who: (letter: string) => string
}>()
const emit = defineEmits<{ play: [seconds: number]; split: [] }>()
const done = ref<{ at: number; id: string }[] | null>(null)

const cuts = ref<number[]>([])
const busy = ref(false)
const error = ref('')
const canHere = computed(() => canCut(props.now, props.duration, cuts.value))
const result = computed(() => parts(cuts.value, props.duration, props.lines))
const names = (letters: string[]) => letters.slice(0, 3).map(props.who).join(', ') + (letters.length > 3 ? '…' : '')
const chosen = (at: number) => cuts.value.includes(at)

function toggle(at: number) {
  if (chosen(at)) cuts.value = cuts.value.filter((c) => c !== at)
  else if (canCut(at, props.duration, cuts.value)) cuts.value = [...cuts.value, at].sort((a, b) => a - b)
}

async function split() {
  busy.value = true
  error.value = ''
  try {
    const r = await lab.split(props.id, cuts.value)
    done.value = r.new_sessions.map((id, i) => ({ at: cuts.value[i], id }))
    emit('split')
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}
</script>

<template>
  <section v-if="done" class="card pad" aria-labelledby="split" role="status">
    <h2 id="split">Split</h2>
    <p class="small"><b>Done.</b> {{ done.length === 1 ? `From ${mss(done[0].at)} is now its own conversation.`
      : `This is now ${done.length + 1} conversations.` }} Each is worked out again (transcript, speakers, numbers);
      names you confirmed carry over.</p>
    <a v-for="(d, i) in done" :key="d.id" class="link" :href="href.conversation(d.id)">Open part {{ i + 2 }}, from {{ mss(d.at) }} →</a>
  </section>
  <section v-else-if="!audioDeleted" class="card pad" aria-labelledby="split">
    <h2 id="split">More than one discussion?</h2>
    <div v-for="(sg, i) in suggestions" :key="sg.at" class="sg">
      <p class="small"><b>{{ i === 0 ? 'The people change' : 'Maybe also' }} at {{ mss(sg.at) }}</b>, after a {{ mss(sg.pause_s) }} pause:
        {{ names(sg.before) }} before; {{ names(sg.after) }} after.</p>
      <div class="buttons">
        <button type="button" class="btn btn-small" @click="emit('play', Math.max(0, sg.at - 10))">Listen around it</button>
        <button type="button" class="btn btn-small" :aria-pressed="chosen(sg.at)"
                :disabled="!chosen(sg.at) && !canCut(sg.at, duration, cuts)" @click="toggle(sg.at)">
          {{ chosen(sg.at) ? '✓ Cut here' : 'Cut here' }}</button>
      </div>
    </div>
    <button v-if="canHere" type="button" class="link" @click="toggle(now)">Add a cut at {{ mss(now) }} (where playback is)</button>
    <p v-else-if="!suggestions.length && !cuts.length" class="muted small">Play to where the next discussion starts to cut there.</p>
    <div v-if="cuts.length" class="confirm" role="group" aria-label="Split">
      <p class="small">It becomes {{ result.length }} conversations. This one is worked out again up to the first cut
        (transcript, speakers and numbers; confirmed names carry over); each other part goes through on its own.</p>
      <ol class="parts small">
        <li v-for="(p, i) in result" :key="p.start">
          <span><b>{{ mss(p.start) }}–{{ mss(p.end) }}</b>
            <span class="muted"> · {{ p.speakers.length ? names(p.speakers) : 'nobody named yet' }}</span></span>
          <button v-if="i > 0" type="button" class="btn btn-small" :aria-label="`Remove the cut at ${mss(p.start)}`"
                  @click="toggle(p.start)">Remove cut</button>
        </li>
      </ol>
      <div class="buttons">
        <button type="button" class="btn btn-small" @click="cuts = []">Cancel</button>
        <button type="button" class="btn btn-small btn-primary" :disabled="busy" @click="split">Split into {{ result.length }}</button>
      </div>
    </div>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.pad { padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; }
h2 { margin: 0; font-size: 15px; font-weight: 600; color: var(--muted); }
p { margin: 0; }
.sg { display: flex; flex-direction: column; gap: 6px; }
.buttons { display: flex; gap: 8px; justify-content: flex-start; flex-wrap: wrap; }
.confirm .buttons { justify-content: flex-end; }
.link { text-decoration: none; border: 0; background: none; color: var(--accent); cursor: pointer; font: inherit; font-size: 14px; padding: 4px 0; text-align: left; }
.confirm { border: 1px solid var(--line); border-radius: 10px; padding: 12px; display: flex; flex-direction: column; gap: 8px; background: var(--quote); }
.parts { margin: 0; padding-left: 20px; display: flex; flex-direction: column; gap: 6px; }
.parts li > span { margin-right: 8px; }
.parts li { overflow-wrap: anywhere; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (pointer: coarse) { .link { min-height: 44px; padding: 12px 0; } }
</style>
