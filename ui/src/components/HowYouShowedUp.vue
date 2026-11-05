<script setup lang="ts">
// How you showed up (B10, 15 Oct: "speak clearly without hesitation like a CEO"): six delivery numbers
// for this conversation, each against his usual. One ink, no colour for good or bad: a row says where the
// number sits in his own spread. Each row opens in place to what it counted, and can become his focus.
// Listening (B13, 19 Oct) is a group under the six: patience, turns, their longest stretch.
// Voice (B27, 21 Oct) a group under Listening, from the stored audio: pitch range, loudness, both when pushed back.
import { computed, ref } from 'vue'
import { DELIVERY_NAME, deliveryValue, spreadText, stateWord, usualText } from '../lab/delivery'
import { measureValue, mss } from '../lab/format'
import { lab } from '../lab/api'
import { href } from '../lab/router'
import { onMac } from '../lab/where'
import type { DeliveryMeasure, DeliveryRow, SessionDelivery } from '../lab/types'

const props = defineProps<{ d: SessionDelivery; canHear: boolean; playing: string | null; markFillers: boolean
  /** How many "you know"s the recorder flashed here (B20), or null when none / not a recorder conversation. */
  cues?: number | null }>()
const emit = defineEmits<{ play: [key: string, start: number, end: number]; focus: [m: DeliveryMeasure];
  'update:markFillers': [on: boolean]; changed: [] }>()

const open = ref<DeliveryMeasure | null>(null)
const toggle = (m: DeliveryMeasure) => { open.value = open.value === m ? null : m }
const PER_100: DeliveryMeasure[] = ['fillers', 'pauses', 'restarts', 'hedges']
const LISTENING: DeliveryMeasure[] = ['patience', 'turns', 'their_longest']
const VOICE: DeliveryMeasure[] = ['pitch_range', 'loudness', 'pushed_pitch', 'pushed_loudness']
const PUSHED: DeliveryMeasure[] = ['pushed_pitch', 'pushed_loudness']
const all = computed(() => [...props.d.rows, ...(props.d.listening ?? []), ...(props.d.voice ?? [])])
// What moved first (28 Oct review, item 8): rows above or below his usual; the rest fold under one line.
const moved = computed(() => all.value.filter((r) => r.state === 'low' || r.state === 'high'))
const rest = computed(() => all.value.filter((r) => r.state !== 'low' && r.state !== 'high'))
const restOpen = ref(false)
// Each group's heading sits above its first row among the rest.
const GROUPS: { name: string; sub?: string; of: DeliveryMeasure[] }[] = [
  { name: 'Listening', of: LISTENING },
  { name: 'Voice', sub: 'from the audio, not checked by ear yet', of: VOICE },
]
const heading = (r: DeliveryRow) => GROUPS.find((g) => g.of.includes(r.measure) && rest.value.find((x) => g.of.includes(x.measure)) === r)
// A short conversation: every row would say "too short to tell", so one line says it instead (UX review, 21 Oct).
const allShort = computed(() => props.d.rows.length > 0 && props.d.rows.every((r) => r.state === 'short'))
const showAnyway = ref(false)
const count = (r: DeliveryRow) => (r.value == null ? 0 : Math.round(r.value * props.d.words / 100))
const words = (n: number) => n.toLocaleString('en-GB')
const list = (by: Record<string, number>) => Object.entries(by).map(([k, n]) => `${k} ×${n}`).join(' · ')

function why(r: DeliveryRow): string {
  switch (r.state) {
    case 'short': return `Under ${props.d.min_words} of your words here (${words(props.d.words)}): too few to compare.`
    case 'no_usual': return `Your usual needs 5 conversations on your line before this one; there ${r.conversations === 1 ? 'is' : 'are'} ${r.conversations}.`
    case 'off': return `Not on your line: ${props.d.reason}.`
    case 'no_audio': return 'The audio was deleted after 60 days, before its voice was measured.'
    case 'unmeasured':
      if (VOICE.includes(r.measure) && props.d.timed_words != null) return voiceWhy(r.measure)
      return r.measure === 'patience' && props.d.timed_words != null
        ? 'You didn\'t take the turn after someone finished here.'
        : 'This transcript has no word times, so this can\'t be counted here.'
    default: return spreadText(r)
  }
}

function voiceWhy(m: DeliveryMeasure): string {
  if (props.d.voice_s == null) return 'Not measured from the audio yet: it will be within the hour.'
  if (m === 'loudness') return 'Nobody else spoke long enough here to compare your level with.'
  if (PUSHED.includes(m)) return props.d.pushed_turns
    ? 'Under 10 s of your voice in those turns, or in the rest of yours: too little to compare.'
    : 'Nobody asked you a question or disagreed with you right before you spoke here.'
  return 'Under 10 s of your voice could be measured here.'
}

// His hedge list, edited in place like the filler list on Progress; the page reloads to count it again.
const hedge = ref('')
const hedgeError = ref('')
const hedgeSaving = ref(false)
async function setHedge(phrase: string, on: boolean) {
  hedgeSaving.value = true
  try {
    await lab.hedgeWord(phrase, on)
    hedge.value = ''
    hedgeError.value = ''
    emit('changed')
  } catch (e) {
    hedgeError.value = e instanceof Error ? e.message : String(e)
  } finally {
    hedgeSaving.value = false
  }
}
</script>

<template>
  <section class="hy" aria-labelledby="hy-h">
    <h3 id="hy-h">How you showed up</h3>
    <p v-if="allShort && !showAnyway" class="small muted">Too short to tell: under {{ d.min_words }} of your words here ({{ words(d.words) }}).
      <button type="button" class="anyway" @click="showAnyway = true; restOpen = true">Show anyway</button></p>
    <ul v-else class="rows">
      <li v-if="!moved.length" class="none small muted">Nothing moved from your usual here.</li>
      <template v-for="r in [...moved, ...(restOpen ? rest : [])]" :key="r.measure">
        <li v-if="restOpen && heading(r)" class="group">
          <h4>{{ heading(r)!.name }}<span v-if="heading(r)!.sub" class="sub"> · {{ heading(r)!.sub }}</span></h4>
        </li>
        <li :class="{ open: open === r.measure }">
          <button type="button" class="row" :aria-expanded="open === r.measure" :aria-controls="`hy-${r.measure}`" @click="toggle(r.measure)">
            <span class="name">{{ DELIVERY_NAME[r.measure] }}</span>
            <span class="val">{{ deliveryValue(r.measure, r.value) }}</span>
            <span class="usual muted">{{ usualText(r) }}</span>
            <span class="state">{{ stateWord(r) }}</span>
            <span class="chev" aria-hidden="true">›</span>
          </button>
          <p v-if="r.groups_higher" class="note small muted">Groups usually run higher for you.</p>
          <div v-if="open === r.measure" :id="`hy-${r.measure}`" class="detail">
            <p class="small">{{ why(r) }}</p>
            <p v-if="PER_100.includes(r.measure) && r.value != null" class="small">{{ count(r) }} in your {{ words(d.words) }} words here.</p>

            <template v-if="r.measure === 'fillers'">
              <p v-if="d.fillers.fillers" class="small">{{ list(d.fillers.by_phrase) }}</p>
              <p v-if="cues" class="small">The recorder flashed “you know” {{ cues }} time{{ cues === 1 ? '' : 's' }}; the
                transcript has {{ d.fillers.by_phrase['you know'] ?? 0 }} of yours. Each flash is marked ◆ on its line.</p>
              <div v-if="d.fillers.heaviest.length && canHear" class="heavy">
                <span class="small muted">Hear the heaviest stretches:</span>
                <button v-for="(h, i) in d.fillers.heaviest" :key="i" type="button" class="mini" :aria-pressed="playing === `h${i}`"
                        @click="emit('play', `h${i}`, h.start, h.end)">{{ playing === `h${i}` ? '❚❚' : '▶' }} {{ mss(h.start) }} · {{ h.fillers }} in {{ h.words }} words</button>
              </div>
              <label class="check small"><input type="checkbox" :checked="markFillers"
                     @change="emit('update:markFillers', ($event.target as HTMLInputElement).checked)"> Underline my fillers in the transcript</label>
              <p class="small muted">Counted with your list, <a :href="href.progress">kept on Progress</a>.</p>
            </template>

            <div v-else-if="r.measure === 'pauses' && d.moments.pauses.length && canHear" class="heavy">
              <span class="small muted">Hear the longest:</span>
              <button v-for="(p, i) in d.moments.pauses" :key="i" type="button" class="mini" :aria-pressed="playing === `pa${i}`"
                      @click="emit('play', `pa${i}`, Math.max(0, p.start - 3), p.end + 2)">{{ playing === `pa${i}` ? '❚❚' : '▶' }} {{ mss(p.start) }} · {{ (p.end - p.start).toFixed(1) }} s</button>
            </div>

            <div v-else-if="r.measure === 'restarts' && d.moments.restarts.length && canHear" class="heavy">
              <span class="small muted">Hear them:</span>
              <button v-for="(p, i) in d.moments.restarts" :key="i" type="button" class="mini" :aria-pressed="playing === `rs${i}`"
                      @click="emit('play', `rs${i}`, Math.max(0, p.start - 1.5), p.end + 1.5)">{{ playing === `rs${i}` ? '❚❚' : '▶' }} {{ mss(p.start) }}</button>
            </div>

            <template v-else-if="r.measure === 'hedges'">
              <p v-if="d.hedges.count" class="small">{{ list(d.hedges.by_phrase) }}</p>
              <p class="small muted">Some hedges are honest: this is a count, not a verdict. Your list:</p>
              <ul class="chips">
                <li v-for="p in d.hedges.phrases" :key="p">{{ p }}
                  <button type="button" class="x" :aria-label="`Take ${p} off the list`" :disabled="hedgeSaving" @click="setHedge(p, false)">×</button></li>
              </ul>
              <form class="add" @submit.prevent="setHedge(hedge, true)">
                <input v-model="hedge" type="text" maxlength="40" placeholder="Add a word or phrase" aria-label="Add a hedge">
                <button type="submit" class="mini" :disabled="hedgeSaving || !hedge.trim()">Add</button>
              </form>
              <p v-if="hedgeError" class="error small" role="alert">{{ hedgeError }}</p>
            </template>

            <p v-else-if="r.measure === 'pace' && d.speak_s" class="small">{{ words(d.timed_words ?? 0) }} words in {{ mss(d.speak_s) }}
              of speaking; pauses of a second or more aren't counted as speaking.</p>

            <div v-else-if="r.measure === 'longest' && d.moments.longest && canHear" class="heavy">
              <button type="button" class="mini" :aria-pressed="playing === 'lg'"
                      @click="emit('play', 'lg', d.moments.longest.start, d.moments.longest.end)">{{ playing === 'lg' ? '❚❚' : '▶' }}
                {{ mss(d.moments.longest.start) }} · {{ d.moments.longest.words }} words</button>
            </div>

            <template v-else-if="r.measure === 'patience'">
              <p class="small">You took the turn {{ d.taken }} time{{ d.taken === 1 ? '' : 's' }} after someone finished<template
                v-if="d.over">, and started before they finished {{ d.over }} time{{ d.over === 1 ? '' : 's' }}: that's talking over,
                not counted here</template>. A gap under 0.2 s reads as zero.</p>
              <div v-if="d.moments.waits.length && canHear" class="heavy">
                <span class="small muted">Hear the longest waits:</span>
                <button v-for="(p, i) in d.moments.waits" :key="i" type="button" class="mini" :aria-pressed="playing === `wt${i}`"
                        @click="emit('play', `wt${i}`, Math.max(0, p.start - 3), p.end + 2)">{{ playing === `wt${i}` ? '❚❚' : '▶' }} {{ mss(p.start) }} · {{ p.gap.toFixed(1) }} s</button>
              </div>
            </template>

            <p v-else-if="r.measure === 'turns' && d.span_s" class="small">The talk changed hands {{ d.changes }} times in {{ mss(d.span_s) }}.
              A word or two while someone carries on (“mm”, “yes”) isn't a change.</p>

            <div v-else-if="r.measure === 'their_longest' && d.moments.their_longest.length" class="heavy">
              <span class="small muted">Each person's longest, before someone took the floor:</span>
              <template v-for="(p, i) in d.moments.their_longest" :key="p.letter">
                <button v-if="canHear" type="button" class="mini" :aria-pressed="playing === `tl${i}`"
                        @click="emit('play', `tl${i}`, p.start, p.end)">{{ playing === `tl${i}` ? '❚❚' : '▶' }} {{ p.name ?? `Speaker ${p.letter}` }} · {{ measureValue('their_longest', p.s) }}</button>
                <span v-else class="small">{{ p.name ?? `Speaker ${p.letter}` }} · {{ measureValue('their_longest', p.s) }}</span>
              </template>
            </div>

            <template v-else-if="VOICE.includes(r.measure) && r.value != null">
              <p v-if="r.measure === 'pitch_range'" class="small">How far your pitch moves: from the low tenth of your voice to the
                high tenth, in {{ Math.round(d.voice_s ?? 0) }} s of it.</p>
              <p v-else-if="r.measure === 'loudness'" class="small">Your level while you speak against the others' in this
                conversation, so where the recorder sat cancels out.</p>
              <p v-else class="small">{{ d.pushed_turns }} turn{{ d.pushed_turns === 1 ? '' : 's' }} of yours right after a question or a
                “no”, “but”, “I don't think”, against the rest of yours here.</p>
              <p class="small muted">Read from the audio, not checked by ear yet.</p>
            </template>

            <button type="button" class="practise" @click="emit('focus', r.measure)">Make it my focus</button>
          </div>
        </li>
      </template>
      <li v-if="rest.length">
        <button type="button" class="fold" :aria-expanded="restOpen" @click="restOpen = !restOpen">
          <span class="fn">{{ restOpen ? 'Fold' : moved.length ? `${rest.length} more` : `All ${rest.length}` }}</span><span
            class="muted small">about usual, or can't tell yet</span><span class="fc" aria-hidden="true">›</span></button>
      </li>
    </ul>
    <p v-if="!allShort || showAnyway" class="small muted">Counted from the transcript, which drops some of what's said<template v-if="d.voice?.length">, and Voice
      from the audio</template>; none of these is checked by ear yet.
      <a v-if="onMac" :href="href.fillerCheck">Check your fillers by ear</a></p>
  </section>
</template>

<style scoped>
.hy { display: flex; flex-direction: column; gap: 8px; }
h3 { margin: 0; font-size: 13px; color: var(--muted); font-weight: 600; }
.rows { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }
.rows > li { border-bottom: 1px solid var(--line); }
.row { width: 100%; display: grid; grid-template-columns: 10rem minmax(0, 1fr) 7.5rem 9rem 1rem; align-items: center; gap: 12px;
  border: 0; background: none; color: var(--ink); font: inherit; font-size: 14px; text-align: left; padding: 8px 2px; min-height: 40px; cursor: pointer; }
.row:hover { background: var(--quote); }
.name { font-weight: 500; }
.val { font-variant-numeric: tabular-nums; }
.state { color: var(--ink-2); }
.chev { color: var(--muted); transition: transform 0.15s; }
.open .chev { transform: rotate(90deg); }
.note { margin: -4px 0 6px 2px; }
.group h4 { margin: 14px 0 4px; font-size: 13px; color: var(--muted); font-weight: 600; }
.group h4 .sub { font-weight: 400; }
.fold { width: 100%; display: grid; grid-template-columns: auto minmax(0, 1fr) 1rem; gap: 10px; align-items: center; border: 0;
  background: none; color: var(--ink); font: inherit; font-size: 14px; text-align: left; padding: 8px 2px; min-height: 40px; cursor: pointer; }
.fn { font-weight: 500; color: var(--accent); }
.fc { color: var(--muted); transition: transform 0.15s; }
.fold[aria-expanded='true'] .fc { transform: rotate(-90deg); }
.none { padding: 8px 2px; }
.detail { padding: 2px 2px 12px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.detail p { margin: 0; }
p { margin: 0; }
.heavy { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.mini { font: inherit; font-size: 13px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); padding: 4px 10px; min-height: 34px; border-radius: 9px; cursor: pointer; }
.mini[aria-pressed='true'] { background: var(--accent-soft); border-color: var(--accent); }
.check { display: flex; align-items: center; gap: 8px; min-height: 32px; }
.chips { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 6px; }
.chips li { font-size: 13px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 4px 2px 10px; display: flex; align-items: center; gap: 2px; }
.x { border: 0; background: none; color: var(--muted); font: inherit; cursor: pointer; min-width: 28px; min-height: 28px; }
.add { display: flex; gap: 6px; }
.add input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 9px; padding: 4px 10px; min-height: 34px; }
.practise { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
  padding: 5px 12px; min-height: 36px; border-radius: 10px; cursor: pointer; }
.anyway { border: 0; background: none; font: inherit; color: var(--accent); cursor: pointer;
  display: inline-block; padding: 12px 6px; margin: -12px -6px; }  /* a 44 px tap area that doesn't move the line */
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 640px) {
  /* Two lines a row: what and where it sits, then the number and the usual. */
  .row { grid-template-columns: minmax(0, 1fr) auto 1rem; grid-template-areas: 'name state chev' 'val usual chev';
    row-gap: 2px; min-height: 44px; }
  .name { grid-area: name; } .state { grid-area: state; text-align: right; } .chev { grid-area: chev; }
  .val { grid-area: val; color: var(--ink-2); } .usual { grid-area: usual; text-align: right; }
  .mini, .practise, .add input { min-height: 44px; }
  .fold { min-height: 44px; }
}
</style>
