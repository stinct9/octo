<script setup lang="ts">
// The transcript, folded: each line plays from there, the owner's fillers underlined, the recorder's cues and marks
// beside the line they followed, and (on the Mac) who said a line moved to someone else.
import { computed, ref } from 'vue'
import { mss, pieces } from '../../lab/format'
import { markedLines } from '../../lab/marks'
import { href } from '../../lab/router'
import { canEdit } from '../../lab/where'
import type { Line, SessionDetail, Speaker } from '../../lab/types'

const props = defineProps<{
  s: SessionDetail
  current: number
  playing: boolean
  t: number
  markFillers: boolean
  color: Record<string, string>
  who: (letter: string | null) => string
  nameOf: (sp: Speaker) => string
}>()
const showLines = defineModel<boolean>('showLines', { required: true })
const emit = defineEmits<{ play: [seconds: number]; move: [line: number, to: string] }>()

const menuFor = ref<number | null>(null)
function moveLine(line: number, to: string) {
  menuFor.value = null
  emit('move', line, to)
}
// Your fillers underlined in your lines (6 Oct): noticing them is what changes the habit.
const fillerSpans = (id: number) => (props.markFillers ? props.s.fillers?.spans[id] ?? null : null)
// The recorder's own "you know" flashes (B20, rec-24), per line: what it caught, beside what the transcript has.
const cuesOn = (id: number) => props.s.recorder_cues?.filter((c) => c.line === id).length ?? 0
const marked = computed(() => markedLines(props.s.marks ?? []))  // B16
// One discussion in parts (B35): on the first part, the later parts run on after its lines, each after its gap. Their
// fixing tools are on their own pages.
const later = computed(() => {
  const list = props.s.parts?.head === props.s.id ? props.s.parts?.list ?? [] : []
  return list.slice(1).map((x) => ({ ...x, lines: (props.s.parts?.lines ?? []).filter((l) => l.part === x.id) as Line[] }))
})
const total = computed(() => props.s.lines.length + later.value.reduce((n, x) => n + x.lines.length, 0))
const gap = (s: number | null) => (s == null ? '' : s < 90 ? `${Math.round(s)} s later` : `${Math.round(s / 60)} min later`)
</script>

<template>
  <section class="card transcript" aria-labelledby="tr">
    <div class="row-head">
      <h2 id="tr">Transcript</h2>
      <span v-if="showLines" class="muted small">Click a line to hear it · ⚠ unsure who or what</span>
      <span class="grow" />
      <button v-if="total" type="button" class="link" :aria-expanded="showLines" @click="showLines = !showLines">
        {{ showLines ? 'Hide' : `Show · ${total} lines` }}</button>
    </div>
    <p v-if="s.paused" class="muted">{{ s.paused }}. Only English and Hindi are transcribed for now. Speakers and
      talk time are kept, and the audio {{ s.audio_days ? `stays ${s.audio_days} days` : 'is kept' }} in case it can be redone
      with a better model.</p>
    <p v-else-if="s.lines.length === 0" class="muted">Not transcribed yet.</p>
    <p v-if="s.merged && s.lines.length && showLines" class="muted small">Hindi: two local drafts (Whisper and Qwen) merged by
      Claude from their text. [unclear] where neither draft could be trusted; those lines are marked ⚠.<template
      v-if="Object.keys(s.unsupported).length"> {{ Object.values(s.unsupported).flat().length }} words, dotted, are in
      neither draft nearby: some are fair corrections, some may not have been said.</template></p>
    <template v-if="showLines">
      <div v-for="ln in s.lines" :id="`line-${ln.id}`" :key="ln.id" class="line"
           :class="{ now: ln.id === current && (playing || t > 0), open: menuFor === ln.id }">
        <button type="button" class="say" @click="emit('play', ln.start)">
          <span class="mono muted small">{{ mss(ln.start) }}</span>
          <span class="who" :class="color[ln.speaker ?? '']">{{ who(ln.speaker) }}</span>
          <span><template v-if="s.unsupported[ln.id]"><template v-for="(tok, k) in ln.text.split(' ')" :key="k"><span
            :class="{ unsup: s.unsupported[ln.id].includes(tok) }" :title="s.unsupported[ln.id].includes(tok) ? 'In neither local draft nearby: listen' : undefined">{{ tok }}</span>{{ ' ' }}</template></template><template
            v-else-if="fillerSpans(ln.id)"><template v-for="(pc, k) in pieces(ln.text, fillerSpans(ln.id) ?? [])" :key="k"><span v-if="pc.on"
            class="filler" title="A filler, by your list">{{ pc.t }}</span><template v-else>{{ pc.t }}</template></template></template><template
            v-else>{{ ln.text }}</template><span v-if="ln.uncertain" class="warn" title="Unsure who said this, or some words"> ⚠</span><span
            v-if="cuesOn(ln.id)" class="cue" :title="`The recorder flashed “you know” here${cuesOn(ln.id) > 1 ? ` (${cuesOn(ln.id)} times)` : ''}`"> ◆</span><span
            v-if="marked.has(ln.id)" class="marked small"> ⚑ You marked this</span></span>
        </button>
        <button v-if="canEdit" type="button" class="line-more" :aria-expanded="menuFor === ln.id" aria-label="Who said this line?"
                @click="menuFor = menuFor === ln.id ? null : ln.id">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1.5" fill="currentColor" /><circle cx="8" cy="8" r="1.5" fill="currentColor" /><circle cx="13" cy="8" r="1.5" fill="currentColor" /></svg>
        </button>
        <div v-if="menuFor === ln.id" class="menu" role="group" :aria-label="`Who said this line`">
          <span class="muted small">Who said this line?</span>
          <button v-for="sp in s.speakers" :key="sp.letter" type="button" :aria-pressed="ln.speaker === sp.letter"
                  @click="moveLine(ln.id, sp.letter)"><i :class="color[sp.letter]" />{{ nameOf(sp) }}</button>
        </div>
      </div>
      <template v-for="x in later" :key="x.id">
        <p class="part muted small" role="separator">… {{ gap(x.gap_s) }} · <a :href="href.conversation(x.id)">part {{ x.n }}</a>
          <template v-if="canEdit"> (who said what is fixed there)</template> …</p>
        <div v-for="ln in x.lines" :id="`line-${ln.id}`" :key="ln.id" class="line" :class="{ now: ln.id === current && (playing || t > 0) }">
          <button type="button" class="say" @click="emit('play', ln.start)">
            <span class="mono muted small">{{ mss(ln.start) }}</span>
            <span class="who" :class="color[ln.speaker ?? '']">{{ who(ln.speaker) }}</span>
            <span>{{ ln.text }}<span v-if="ln.uncertain" class="warn" title="Unsure who said this, or some words"> ⚠</span></span>
          </button>
        </div>
        <p v-if="!x.lines.length" class="muted small">Not transcribed yet.</p>
      </template>
      <details v-if="s.dropped.length" class="dropped muted small">
        <summary>{{ s.dropped.length }} stretch{{ s.dropped.length === 1 ? '' : 'es' }} dropped as silence: hear whether they were speech</summary>
        <button v-for="d in s.dropped" :key="d.start" type="button" class="say gone" @click="emit('play', d.start)">
          <span class="mono">{{ mss(d.start) }}</span><span /><span>{{ d.text }}</span>
        </button>
      </details>
    </template>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
.grow { flex-grow: 1; min-width: 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.warn { color: var(--warn); }
.cue { color: var(--accent, currentColor); }
.marked { color: var(--accent, currentColor); }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.menu i { background: var(--c); }
.who { color: var(--c); }

.transcript { padding: 14px 18px; display: flex; flex-direction: column; gap: 8px; }
.row-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.row-head .link { min-height: 36px; }
.line { position: relative; display: flex; align-items: flex-start; border-radius: 8px; }
.line:hover, .line.open { background: var(--quote); }
.line.now { background: var(--accent-soft); }
.say { flex-grow: 1; display: grid; grid-template-columns: 48px 110px minmax(0, 1fr); gap: 12px; text-align: left;
  padding: 8px 10px; border: 0; background: transparent; cursor: pointer; line-height: 1.5; min-width: 0; color: var(--ink); font: inherit; }
.say .who { font-weight: 600; }
.line-more { width: 36px; height: 36px; margin: 2px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); cursor: pointer; opacity: 0; }
.line:hover .line-more, .line.open .line-more, .line-more:focus-visible { opacity: 1; }
.menu { position: absolute; right: 0; top: 40px; z-index: 3; width: 240px; padding: 8px; display: flex; flex-direction: column; gap: 2px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18); }
.menu span { padding: 4px 8px; }
.menu button { display: flex; align-items: center; gap: 10px; text-align: left; border: 0; background: transparent; padding: 8px; min-height: 40px; border-radius: 8px; cursor: pointer; }
.menu button[aria-pressed='true'] { background: var(--accent-soft); }
.menu i { width: 10px; height: 10px; border-radius: 2px; }
.unsup { text-decoration: underline dotted var(--warn); text-underline-offset: 3px; }
.filler { text-decoration: underline 2px var(--accent); text-decoration-color: color-mix(in srgb, var(--accent) 55%, transparent); text-underline-offset: 3px; }
.part { margin: 8px 0 0; text-align: center; }
.dropped summary { cursor: pointer; min-height: 36px; display: flex; align-items: center; }
.say.gone { width: 100%; color: var(--muted); text-decoration: line-through; text-decoration-color: var(--line); }

@media (max-width: 760px) {
  .link { min-height: 44px; }
  .say { grid-template-columns: 44px minmax(0, 1fr); }
  .say .who { grid-column: 2; }
  .say > span:last-child { grid-column: 2; }
  .line-more { opacity: 1; }
}
</style>
