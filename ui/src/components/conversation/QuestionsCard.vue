<script setup lang="ts">
// Others' questions and who spoke next (B4), folded like the transcript.
import { mss } from '../../lab/format'
import { pref } from '../../lab/remembered'
import type { SessionDetail } from '../../lab/types'

const props = defineProps<{
  questions: NonNullable<SessionDetail['questions']>
  lineById: Record<number, SessionDetail['lines'][number]>
  who: (letter: string | null) => string
  color: Record<string, string>
  playingClip: string | null
}>()
const emit = defineEmits<{ play: [item: string, start: number, end: number] }>()

const showQs = pref('octo:questions', false)
const lineText = (id: number) => props.lineById[id]?.text ?? ''
</script>

<template>
  <section class="card questions" aria-labelledby="qh">
    <div class="row-head">
      <h2 id="qh">Questions from others</h2>
      <span class="muted small">{{ questions.asked }} · you spoke next after {{ questions.you }}</span>
      <span class="grow" />
      <button type="button" class="link" :aria-expanded="showQs" @click="showQs = !showQs">{{ showQs ? 'Hide' : 'Show' }}</button>
    </div>
    <template v-if="showQs">
      <p class="muted small">Who spoke next after each question someone else asked, from the transcript's question marks.
        {{ questions.size === 'group' ? "In a group, many questions aren't for you." : '' }} It can't tell whether
        what was said answered the question.</p>
      <ol class="qs">
        <li v-for="m in questions.moments" :key="m.line">
          <button type="button" class="q" :aria-pressed="playingClip === `q-${m.line}`" @click="emit('play', `q-${m.line}`, m.start, m.end)">
            <span class="ts">▶ {{ mss(m.start) }}</span><span><b :class="['who', color[m.by]]">{{ who(m.by) }}</b> “{{ lineText(m.line) }}”</span></button>
          <div class="q-out small">
            <span :class="m.outcome === 'you' ? 'ok' : 'muted'">{{ m.outcome === 'you' ? 'You spoke next' : m.outcome === 'other'
              ? `${who(lineById[m.reply ?? -1]?.speaker ?? null)} spoke next` : `Nobody spoke within ${questions.reply_s} s` }}</span>
            <button v-if="m.reply !== null && lineById[m.reply]" type="button" class="link"
                    @click="emit('play', `r-${m.line}`, lineById[m.reply].start, lineById[m.reply].end)">hear it</button>
          </div>
        </li>
      </ol>
    </template>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
.grow { flex-grow: 1; min-width: 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.who { color: var(--c); }
.questions { padding: 14px 18px; display: flex; flex-direction: column; gap: 8px; }
.qs { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.qs .q { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 8px; width: 100%; text-align: left; border: 0; background: var(--quote);
  border-radius: 8px; padding: 8px 10px; font: inherit; color: inherit; cursor: pointer; min-height: 44px; }
.qs .ts { color: var(--accent); font-variant-numeric: tabular-nums; }
.q-out { display: flex; gap: 10px; align-items: center; padding: 2px 10px 0 82px; }
.q-out .link { min-height: 36px; }
@media (max-width: 640px) { .q-out { padding-left: 10px; } }
.row-head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
.row-head .link { min-height: 36px; }

@media (max-width: 760px) {
  .link { min-height: 44px; }
}
</style>
