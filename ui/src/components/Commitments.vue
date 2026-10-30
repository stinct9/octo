<script setup lang="ts">
// Commitments both ways (B14): what you promised and what they promised you, each with its line. On a
// conversation page the page hands it the items and plays the lines; on a person's page it loads the open
// ones with them itself, so that page only places it. Marks are notes on what was said: never "acted".
import { computed, onMounted, ref, watch } from 'vue'
import { lab } from '../lab/api'
import { agreedText, MARKED, marks } from '../lab/commitments'
import { day, dueDay, mss, today } from '../lab/format'
import { href } from '../lab/router'
import type { Commitment, CommitmentState, SessionCommitments } from '../lab/types'

const props = defineProps<{
  /** The conversation page's block; or `person` to load the open ones with them. */
  block?: SessionCommitments | null
  person?: string
  name?: string
  /** The conversation's day, for the copied text's heading. */
  at?: number | null
  connected?: boolean
  canHear?: boolean
  playing?: string | null
}>()
const emit = defineEmits<{ play: [item: string, start: number, end: number] }>()

const items = ref<Commitment[]>([])
const tt = ref(!!props.connected)
watch(() => props.block, (b) => { if (b) items.value = [...b.items] }, { immediate: true })
onMounted(async () => {
  if (!props.person) return
  try {
    const got = await lab.commitments(props.person)
    items.value = got.items
    tt.value = got.ticktick_connected
  } catch { /* the rest of the page still shows */ }
})

const error = ref('')
const busy = ref<string | null>(null)
async function act(c: Commitment, call: () => Promise<Commitment>) {
  busy.value = c.id
  error.value = ''
  try {
    const got = await call()
    items.value = items.value.map((x) => (x.id === got.id ? got : x))
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = null }
}
const mark = (c: Commitment, s: CommitmentState | null) => act(c, () => lab.markCommitment(c.id, s))
const send = (c: Commitment) => act(c, () => lab.commitmentTicktick(c.id))

const copied = ref(false)
const heading = computed(() => (props.person ? `What we agreed${props.name ? `, ${props.name} and me` : ''}`
  : `What we agreed${props.at ? `, ${day(props.at)}` : ''}`))
async function copy() {
  try {
    await navigator.clipboard.writeText(agreedText(items.value, heading.value))
    copied.value = true
    setTimeout(() => { copied.value = false }, 2000)
  } catch { error.value = "Couldn't reach the clipboard here" }
}
const overdue = (c: Commitment) => !!c.due && !c.state && c.due < today()
const shown = computed(() => !!items.value.length || (!props.person && !!props.block && props.block.status !== 'old'))
const NOW: Record<string, string> = {
  waiting: 'Looked for after the review.', queued: 'Looking for commitments soon…', running: 'Looking for commitments…',
}
</script>

<template>
  <section v-if="shown" class="card cm" aria-labelledby="cm-h">
    <div class="head">
      <h2 id="cm-h">{{ person ? `Open commitments with ${name ?? 'them'}` : 'Commitments' }}</h2>
      <span class="muted small">both ways</span>
      <span class="grow" />
      <button v-if="items.length" type="button" class="link small" @click="copy">{{ copied ? 'Copied' : 'Copy what we agreed' }}</button>
    </div>
    <p v-if="block && !items.length" class="muted small">{{ block.status === 'done' ? 'No promise of anything concrete heard here.'
      : block.status === 'failed' ? `Couldn't look for them: ${block.error ?? 'it failed'}. It's tried again later.` : NOW[block.status] ?? '' }}</p>
    <ul v-if="items.length" class="list">
      <li v-for="c in items" :key="c.id" :class="{ quiet: !!c.state }">
        <div class="row">
          <button v-if="canHear" type="button" class="hear" :aria-pressed="playing === `c-${c.id}`" :title="`Hear it at ${mss(c.start)}`"
                  @click="emit('play', `c-${c.id}`, c.start, c.end)">▶</button>
          <span class="who small">{{ c.mine ? `You → ${c.to}` : `${c.by} → you` }}</span>
          <span class="what">{{ c.what }}</span>
          <span v-if="c.due" class="due" :class="{ late: overdue(c) }"><template v-if="overdue(c)">Late · </template>{{ dueDay(c.due) }}</span>
          <template v-if="c.state">
            <span class="small" :class="c.state === 'done' || c.state === 'did' ? 'ok' : 'muted'">{{ MARKED[c.state] }}</span>
            <button type="button" class="link small" :disabled="busy === c.id" @click="mark(c, null)">Undo</button>
          </template>
          <template v-else>
            <template v-if="c.mine">
              <a v-if="c.ticktick" :href="c.ticktick.url" target="_blank" rel="noopener" class="in small">✓ TickTick ↗</a>
              <button v-else-if="tt" type="button" class="btn small" :disabled="busy === c.id" @click="send(c)">+ TickTick</button>
            </template>
            <button v-for="m in marks(c.mine)" :key="m.state" type="button" class="btn small" :disabled="busy === c.id"
                    @click="mark(c, m.state)">{{ m.label }}</button>
          </template>
        </div>
        <p class="q small">“{{ c.quote }}”<a v-if="person" class="open" :href="href.conversation(c.session_id)">{{ c.at ? day(c.at) : 'Open' }}</a></p>
      </li>
    </ul>
    <div v-if="block?.came_up.length" class="came">
      <h3 class="small">Came up again</h3>
      <ul class="list">
        <li v-for="c in block.came_up" :key="c.id">
          <div class="row">
            <button v-if="canHear" type="button" class="hear" :aria-pressed="playing === `u-${c.id}`" title="Hear where it came up"
                    @click="emit('play', `u-${c.id}`, c.here.start, c.here.end)">▶</button>
            <span class="who small">{{ c.mine ? `You → ${c.to}` : `${c.by} → you` }}</span>
            <span class="what">{{ c.what }}</span>
            <a class="open small" :href="href.conversation(c.session_id)">{{ c.at ? day(c.at) : 'Open' }}</a>
          </div>
          <p class="q small">{{ c.note }} “{{ c.here.quote }}”</p>
        </li>
      </ul>
    </div>
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <p class="muted small">Only promises of something concrete, each checked against the transcript. Marking one is a note for you;
      it doesn't count as acted on.</p>
  </section>
</template>

<style scoped>
.cm { padding: 16px 20px; display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
h3 { margin: 4px 0; font-weight: 600; color: var(--ink-2); }
.grow { flex: 1; }
p { margin: 0; }
.list { list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.list li { padding: 8px 12px; border-top: 1px solid var(--line); display: flex; flex-direction: column; gap: 4px; }
.list li:first-child { border-top: 0; }
.row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; min-height: 40px; }
.who { color: var(--muted); white-space: nowrap; }
.what { flex: 1; min-width: 10rem; font-weight: 500; }
.quiet .what { color: var(--muted); text-decoration: line-through; text-decoration-color: var(--line-strong); }
.q { color: var(--ink-2); padding-left: 2px; }
.due { font-size: 12px; color: var(--ink-2); border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; white-space: nowrap; }
.due.late { color: var(--warn); border-color: var(--warn); }
.hear { border: 1px solid var(--line-strong); background: var(--surface); color: var(--accent); border-radius: 999px; width: 30px; height: 30px;
  cursor: pointer; font-size: 11px; flex-shrink: 0; }
.hear[aria-pressed='true'] { background: var(--accent-soft); }
.btn { font: inherit; border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink-2); padding: 4px 11px;
  min-height: 32px; border-radius: 10px; cursor: pointer; white-space: nowrap; }
.btn:disabled { opacity: 0.6; cursor: default; }
.in, .open { color: var(--accent); text-decoration: none; white-space: nowrap; margin-left: 8px; }
.link { border: 0; background: none; color: var(--accent); cursor: pointer; padding: 4px 0; font: inherit; min-height: 32px; }
.ok { color: var(--pass); }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 640px) {
  .cm { padding: 14px 16px; }
  .btn, .link, .hear { min-height: 44px; }
  .hear { width: 44px; }
  .what { flex-basis: 100%; order: -1; }
}
</style>
