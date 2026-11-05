<script setup lang="ts">
// Who's who and the numbers, each in one line that opens: fixing the speakers, and the table of everyone's numbers.
import { computed, ref } from 'vue'
import SpeakerRow from '../SpeakerRow.vue'
import { eventsText } from '../../lab/calendar'
import { mss } from '../../lab/format'
import { href } from '../../lab/router'
import { canEdit } from '../../lab/where'
import type { Invitee, SessionDetail, Speaker, Todo } from '../../lab/types'

const props = defineProps<{
  s: SessionDetail
  color: Record<string, string>
  nameOf: (sp: Speaker | undefined) => string
  known: string[]
  othersOf: Record<string, { letter: string; name: string }[]>
  invitees: Invitee[]
  hasSample: (letter: string) => boolean
  playingClip: string | null
  fixError: string
}>()
const showSpeakers = defineModel<boolean>('showSpeakers', { required: true })
const emit = defineEmits<{
  changed: [r: { speakers: Speaker[]; todo: Todo }]
  sample: [letter: string]
  merge: [letter: string, into: string]
  undo: []
  split: []
}>()

const showNumbers = ref(false)
// Numbers table: most talk first.
const byTalk = computed(() => [...props.s.speakers].sort((a, b) =>
  (props.s.numbers?.speakers[b.letter]?.talk_s ?? 0) - (props.s.numbers?.speakers[a.letter]?.talk_s ?? 0)))
// The shares in one line. The owner's own are in How you showed up (B10), the rest in the table.
const numbersLine = computed(() => {
  const n = props.s.numbers
  if (!n) return ''
  return byTalk.value.slice(0, 3).map((sp) => `${props.nameOf(sp)} ${Math.round((n.speakers[sp.letter]?.share ?? 0) * 100)}%`).join(' · ')
})
</script>

<template>
  <section class="card strip" aria-label="Speakers and numbers">
    <div class="strip-row">
      <span class="label">Speakers</span>
      <span class="names">
        <span v-for="sp in s.speakers" :key="sp.letter" class="name"><i :class="color[sp.letter]" aria-hidden="true" /><a
          v-if="sp.person_id && !sp.self && (sp.state === 'confirmed' || sp.state === 'auto')" class="who" :href="href.person(sp.person_id)"
          :title="`Before you talk to ${sp.name}`">{{ nameOf(sp) }}</a><template v-else>{{ nameOf(sp) }}</template></span>
      </span>
      <button type="button" class="link" :aria-expanded="showSpeakers" aria-controls="speakers-panel" @click="showSpeakers = !showSpeakers">
        {{ showSpeakers ? 'Close' : 'Edit' }}</button>
    </div>
    <div v-if="showSpeakers" id="speakers-panel" class="panel">
      <p v-if="s.todo.kind === 'confirm'" class="warn small">Confirm who's who; then the review runs, so nothing is said about the wrong person.</p>
      <p v-else-if="s.todo.kind === 'not_you'" class="muted small">You're not in this one.</p>
      <p v-if="eventsText(s.calendar)" class="muted small">{{ eventsText(s.calendar) }}</p>
      <SpeakerRow v-for="sp in s.speakers" :key="sp.letter" :speaker="sp" :color="color[sp.letter]"
                  :session-id="s.id" :known="known" :others="othersOf[sp.letter]" @changed="emit('changed', $event)"
                  :invitees="sp.self ? [] : invitees"
                  :can-merge="canEdit" :can-hear="!s.audio_deleted && hasSample(sp.letter)"
                  :hearing="playingClip === `sp-${sp.letter}`" @sample="emit('sample', sp.letter)" @merge="emit('merge', sp.letter, $event)" />
      <p v-for="q in s.same_person_asks" :key="q.letters.join()" class="outdated small">{{ q.letters.map((l) => `Speaker ${l}`).join(' and ') }}
        are both named {{ q.name }}, but their voices don't clearly match. The same person?
        <template v-if="canEdit"><button type="button" class="link" @click="emit('merge', q.letters[1], q.letters[0])">Yes, merge
          them</button> · if not, rename one of them.</template><template v-else>Merge them, or rename one, on your Mac.</template></p>
      <div v-if="s.corrections && canEdit" class="small muted">{{ s.corrections }} fix{{ s.corrections === 1 ? '' : 'es' }} to who said what ·
        <button type="button" class="link" @click="emit('undo')">Undo the last</button></div>
      <p v-if="fixError" class="error small" role="alert">{{ fixError }}</p>
      <p class="muted small">Naming someone saves their voiceprint on this Mac, so they're recognised next time.
        Unnamed voices can stay as they are.</p>
    </div>
    <template v-if="s.numbers">
      <div class="strip-row">
        <span class="label">Numbers</span>
        <span class="nums">{{ numbersLine }}</span>
        <button type="button" class="link" :aria-expanded="showNumbers" aria-controls="numbers-panel" @click="showNumbers = !showNumbers">
          {{ showNumbers ? 'Close' : 'All numbers' }}</button>
      </div>
      <div v-if="showNumbers" id="numbers-panel" class="panel">
        <!-- A row per speaker, so any number of people fits; scrolls sideways if the card is narrow. -->
        <div class="scroll">
          <table>
            <thead><tr><th class="l">Speaker</th><th class="r">Talk</th><th class="r">Share</th><th class="r">Turns</th>
              <th class="r">Longest</th><th class="r">Questions¹</th><th class="r">Talked over others²</th><th class="r">Was talked over²</th><th class="r">Fillers³</th></tr></thead>
            <tbody>
              <tr v-for="sp in byTalk" :key="sp.letter">
                <td class="who-cell"><i class="sw" :class="color[sp.letter]" aria-hidden="true" />{{ nameOf(sp) }}</td>
                <td class="r">{{ mss(s.numbers.speakers[sp.letter]?.talk_s ?? 0) }}</td>
                <td class="r">{{ Math.round((s.numbers.speakers[sp.letter]?.share ?? 0) * 100) }}%</td>
                <td class="r">{{ s.numbers.speakers[sp.letter]?.turns }}</td>
                <td class="r">{{ mss(s.numbers.speakers[sp.letter]?.longest_turn_s ?? 0) }}</td>
                <td class="r">{{ s.numbers.speakers[sp.letter]?.questions }}</td>
                <td class="r">{{ s.numbers.speakers[sp.letter]?.interrupted_others }}</td>
                <td class="r">{{ s.numbers.speakers[sp.letter]?.was_interrupted }}</td>
                <td class="r">{{ s.numbers.speakers[sp.letter]?.fillers_per_100_words ?? '–' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted small">¹ From punctuation, approx. ² Started while someone was talking and they stopped; approx.
          ³ Per 100 words: um, uh, ah, you know, kind of, sort of, where the transcription kept them; approx.</p>
      </div>
    </template>
    <div v-if="s.split_suggestions.length && !s.split_suggestions.some((g) => g.strong)" class="strip-row">
      <span class="label">Split?</span>
      <span class="nums muted">This may be more than one conversation: the people change partway.</span>
      <button v-if="canEdit" type="button" class="link" @click="emit('split')">Split…</button>
      <span v-else class="muted small">Split it on your Mac.</span>
    </div>
  </section>
</template>

<style scoped>
.muted { color: var(--muted); }
.small { font-size: 13px; }
.warn { color: var(--warn); }
.error { color: var(--fail); margin: 0; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.outdated { margin: 0; padding: 10px 12px; border-radius: 10px; background: var(--warn-soft); color: var(--ink); }
.name i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; flex-shrink: 0; }
.name i, .who-cell .sw { background: var(--c); }
.who { color: var(--c); }

.strip { padding: 4px 18px; }
.strip-row { display: flex; align-items: center; gap: 14px; min-height: 46px; border-top: 1px solid var(--line); padding: 6px 0; }
.strip-row:first-child { border-top: 0; }
.label { width: 76px; flex-shrink: 0; font-size: 13px; color: var(--muted); font-weight: 500; }
.names { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; gap: 4px 14px; }
.name { display: inline-flex; align-items: center; gap: 6px; font-weight: 500; }
.name .who { color: inherit; text-decoration: underline; text-decoration-color: var(--line-strong); text-underline-offset: 3px;
  display: inline-block; padding: 11px 4px; margin: -11px -4px; }  /* a 44 px tap area that doesn't move the line */
.name .who:hover { color: var(--accent); text-decoration-color: currentColor; }
.nums { flex: 1; min-width: 0; color: var(--ink-2); }
.strip .link { white-space: nowrap; min-height: 36px; }
.panel { padding: 4px 0 14px; display: flex; flex-direction: column; gap: 8px; }
.panel p { margin: 0; }

table { border-collapse: collapse; font-size: 14px; width: 100%; }
th { font-size: 12px; font-weight: 500; color: var(--muted); padding: 6px 5px; vertical-align: bottom; line-height: 1.25; }
th.l { text-align: left; }
.scroll { overflow-x: auto; margin: 0 -4px; }
.who-cell { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
/* The name stays put while the numbers scroll sideways. */
.scroll th.l, .scroll .who-cell { position: sticky; left: 0; background: var(--surface); z-index: 1; }
.who-cell .sw { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
td { padding: 7px 5px; border-top: 1px solid var(--line); white-space: nowrap; }
.r { text-align: right; }

@media (max-width: 760px) {
  .strip { padding: 2px 14px; }
  .strip-row { flex-wrap: wrap; gap: 4px 12px; }
  .label { width: auto; flex-basis: 100%; }
  .strip .link, .link { min-height: 44px; }
  p .link { min-height: 0; padding: 12px 0; margin: -12px 0; }  /* in a line of text: the tap area grows, the line doesn't */
}
</style>
