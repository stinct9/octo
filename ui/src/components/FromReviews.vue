<script setup lang="ts">
// Home's "From your reviews": every open "What to do" step from current reviews, with "Did it"
// (review 4 Oct, M01). Until then the only place to mark one acted was inside each past conversation.
// "Did it" is the owner's own tap, through the same feedback route as the conversation page. "Let it go"
// (6 Oct) takes a step off the list without counting it as done: otherwise the list only grows.
// Since 5 Nov (review item 5) the owner's own commitments are here too, "you promised", each once: one that is the
// same promise as a step is the step, and closing it closes both (the server's). A promise alone is marked through
// its commitment, so it never counts as acted (B14).
// Since 12 Nov (B40) newest first, and "Let the older ones go" at the foot: each item over two weeks old gets its
// own Let it go on the server in one go; Undo right after is each one's own Let it go undone.
import { computed, onMounted, ref } from 'vue'
import { lab } from '../lab/api'
import { dueDay, today as localToday, when } from '../lab/format'
import { href } from '../lab/router'
import type { OpenTask } from '../lab/types'
import FoldToggle from './FoldToggle.vue'

// On a person's page (6 Oct): only the steps from conversations with them, under their own heading.
const props = defineProps<{ only?: string[]; title?: string }>()
const tasks = ref<OpenTask[] | null>(null)
const ttDone = ref<Record<string, boolean | null>>({})
// Marked during this visit: they stay (with Undo and a note) until the page is left.
const done = ref<Record<string, { note: string; saved: string; error: string }>>({})
const letGone = ref<Record<string, boolean>>({})
const error = ref('')
const showAll = ref(false)
const unfolded = ref(true)
const FOLDED = 5
const OLDER_DAYS = 14  // the server's SV.OLDER_DAYS
const asking = ref(false)
const batch = ref<OpenTask[]>([])  // the last "Let the older ones go", for its Undo

const key = (t: OpenTask) => `${t.session_id}/${t.item}`
const today = localToday()
const shown = computed(() => (showAll.value ? tasks.value ?? [] : (tasks.value ?? []).slice(0, FOLDED)))
const older = computed(() => {
  const cut = Date.now() / 1000 - OLDER_DAYS * 86400
  return (tasks.value ?? []).filter((t) => t.at && t.at < cut && !done.value[key(t)] && !letGone.value[key(t)])
})
const open = computed(() => (tasks.value ?? []).filter((t) => !done.value[key(t)] && !letGone.value[key(t)]).length)

async function load() {
  try {
    const got = (await lab.actions()).filter((t) => !props.only || props.only.includes(t.session_id))
    // Keep the ones marked this visit in place, so Undo stays where it was.
    const kept = (tasks.value ?? []).filter((t) => (done.value[key(t)] || letGone.value[key(t)]) && !got.some((g) => key(g) === key(t)))
    tasks.value = [...got, ...kept]
    error.value = ''
  } catch (e) {
    error.value = `Couldn't load your steps: ${e instanceof Error ? e.message : e}`
  }
}
onMounted(async () => {
  await load()
  if ((tasks.value ?? []).some((t) => t.ticktick)) {
    lab.actionsTicktick().then((r) => { ttDone.value = r.done }).catch(() => {})  // unknown: just no hint
  }
})

async function mark(t: OpenTask, acted: boolean) {
  const k = key(t)
  try {
    if (t.kind === 'promise') await lab.markCommitment(t.item, acted ? 'done' : null)
    else await lab.feedback(t.session_id, t.run!, t.item, { acted })
    if (acted) done.value = { ...done.value, [k]: { note: '', saved: '', error: '' } }
    else { const d = { ...done.value }; delete d[k]; done.value = d }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
async function letGo(t: OpenTask, on: boolean) {
  const k = key(t)
  try {
    if (t.kind === 'promise') await lab.markCommitment(t.item, on ? 'dropped' : null)
    else await lab.feedback(t.session_id, t.run!, t.item, { let_go: on })
    if (on) letGone.value = { ...letGone.value, [k]: true }
    else { const d = { ...letGone.value }; delete d[k]; letGone.value = d }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
async function letOlderGo() {
  asking.value = false
  try {
    const gone = new Set((await lab.actionsLetGo(older.value.map(key))).let_go)
    batch.value = (tasks.value ?? []).filter((t) => gone.has(key(t)))
    letGone.value = { ...letGone.value, ...Object.fromEntries([...gone].map((k) => [k, true])) }
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  }
}
async function undoBatch() {
  for (const t of batch.value) await letGo(t, false)
  batch.value = []
}
async function saveNote(t: OpenTask) {
  const d = done.value[key(t)]
  if (!d || d.note.trim() === d.saved || !t.run) return
  try {
    await lab.feedback(t.session_id, t.run, t.item, { note: d.note.trim() })
    d.saved = d.note.trim()
    d.error = ''
  } catch (e) {
    d.error = e instanceof Error ? e.message : String(e)
  }
}
</script>

<template>
  <section v-if="tasks && tasks.length" class="card steps" aria-labelledby="from-reviews">
    <div class="head">
      <h2 id="from-reviews">{{ title ?? 'From your reviews' }}</h2>
      <span class="muted small">{{ open }} open · mark what you did</span>
      <FoldToggle v-if="!only" v-model="unfolded" name="reviews" :label="title ?? 'From your reviews'" />
    </div>
    <template v-if="unfolded || only">
    <p v-if="error" class="error small" role="alert">{{ error }}</p>
    <ul>
      <li v-for="t in shown" :key="key(t)" :class="{ marked: done[key(t)] || letGone[key(t)] }">
        <div class="row">
          <a class="title" :href="`${href.conversation(t.session_id)}`">{{ t.title }}</a>
          <span v-if="t.due" class="due" :class="{ late: t.due < today && !done[key(t)] }"><template v-if="t.due < today && !done[key(t)]">Late · </template>{{ dueDay(t.due) }}</span>
          <span v-if="ttDone[key(t)]" class="tt-done small">Done in TickTick</span>
          <a v-else-if="t.ticktick" :href="t.ticktick" target="_blank" rel="noopener" class="tt small">TickTick ↗</a>
          <template v-if="done[key(t)]">
            <span class="ok small">✓ Did it</span>
            <button type="button" class="link small" @click="mark(t, false)">Undo</button>
          </template>
          <template v-else-if="letGone[key(t)]">
            <span class="muted small">Let go</span>
            <button type="button" class="link small" @click="letGo(t, false)">Undo</button>
          </template>
          <template v-else>
            <button type="button" class="link small" title="Off this list; not counted as done" @click="letGo(t, true)">Let it go</button>
            <button type="button" class="btn btn-small" @click="mark(t, true)">Did it</button>
          </template>
        </div>
        <div class="meta muted small"><template v-if="t.kind === 'promise'">{{ t.to ? `You promised ${t.to}` : 'You promised' }} ·
          </template><template v-else-if="t.promise">You promised this too · </template>{{ when(t.at) }}<template
            v-if="!t.counts"> · not counted</template></div>
        <label v-if="done[key(t)] && t.kind === 'step'" class="note small">What you did (optional)
          <input v-model="done[key(t)].note" maxlength="1000" @blur="saveNote(t)" @keydown.enter.prevent="saveNote(t)">
          <span v-if="done[key(t)].error" class="error">{{ done[key(t)].error }}</span>
          <span v-else-if="done[key(t)].saved" class="muted">Saved</span>
        </label>
      </li>
    </ul>
    <button v-if="tasks.length > FOLDED" type="button" class="link small more" @click="showAll = !showAll">
      {{ showAll ? 'Show fewer' : `Show all ${tasks.length}` }}</button>
    <div v-if="!only && batch.length" class="older small">
      <span class="muted">Let {{ batch.length }} go</span>
      <button type="button" class="link small" @click="undoBatch">Undo</button>
    </div>
    <div v-else-if="!only && older.length" class="older small">
      <template v-if="asking">
        <span>Let {{ older.length }} {{ older.length === 1 ? 'step' : 'steps and promises' }} from more than two weeks ago
          go? They leave this list and aren't counted as done; you can undo it right after.</span>
        <span class="older-actions">
          <button type="button" class="btn btn-small" @click="letOlderGo">Let them go</button>
          <button type="button" class="link small" @click="asking = false">Cancel</button>
        </span>
      </template>
      <button v-else type="button" class="link small" @click="asking = true">
        Let the {{ older.length }} older than two weeks go</button>
    </div>
    </template>
  </section>
</template>

<style scoped>
.steps { padding: 18px 22px; display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; }
h2 { margin: 0; font-size: 17px; font-weight: 600; flex-grow: 1; }
@media (max-width: 640px) { .head > .muted { order: 1; flex-basis: 100%; } }  /* the fold beside the title, the count under it */
ul { list-style: none; margin: 0; padding: 0; }
li { padding: 8px 0; border-top: 1px solid var(--line); }
li:first-child { border-top: 0; }
.row { display: flex; align-items: center; gap: 10px; min-height: 44px; }
.title { flex: 1; min-width: 0; font-weight: 500; color: var(--ink); text-decoration: none; }
.title:hover { text-decoration: underline; }
.marked .title { color: var(--muted); text-decoration: line-through; }
.due { font-size: 12px; color: var(--ink-2); border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; white-space: nowrap; }
.due.late { color: var(--warn); border-color: var(--warn); }
.tt { color: var(--accent); text-decoration: none; white-space: nowrap; }
.tt-done { color: var(--pass); white-space: nowrap; }
.ok { color: var(--pass); }
.btn-small { min-height: 36px; }
.meta { margin-top: -4px; }
.note { display: flex; flex-direction: column; gap: 4px; color: var(--muted); margin-top: 6px; }
.note input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; min-height: 40px; }
.link { border: 0; background: none; color: var(--accent); cursor: pointer; padding: 0; font: inherit; min-height: 32px; }
.more { align-self: flex-start; }
.older { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 10px; }
.older-actions { display: flex; align-items: center; gap: 14px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
p { margin: 0; }
@media (max-width: 640px) {
  .steps { padding: 14px 16px; }
  .row { flex-wrap: wrap; }
  .title { flex-basis: 100%; }
}
</style>
