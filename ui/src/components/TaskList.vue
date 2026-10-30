<script setup lang="ts">
// A review's "What to do", one line per step: due date, TickTick, Did it. Details fold underneath,
// with Practise this, Let it go (6 Oct: off home's list, not counted as acted) and Not right?.
import { computed, ref, watch } from 'vue'
import type { FeedbackChange } from '../lab/api'
import { dueDay, today as localToday } from '../lab/format'
import type { ItemFeedback, SentTask, Task } from '../lab/types'
import NotRight from './NotRight.vue'

const props = defineProps<{
  tasks: Task[]
  feedback: Record<string, ItemFeedback>
  sent: Record<string, SentTask>
  connected: boolean
  sending: string | null
  settingsHref: string
  /** Completed in TickTick (true), not yet, or unknown; only for sent tasks. */
  ttDone?: Record<string, boolean | null>
  /** The item that's the current focus, if it's one of these. */
  focusItem?: string | null
  /** The step the one thing points to (its number), shown as such. */
  oneStep?: number | null
  /** A step to show open and highlighted (from the one thing's link). */
  highlight?: string | null
}>()
const emit = defineEmits<{ change: [item: string, change: FeedbackChange]; ticktick: [item: string]; ticktickAll: []; practise: [item: string] }>()

const rows = computed(() => props.tasks.map((t, i) => {
  const id = `n${i}`
  const fb = props.feedback[id]
  return {
    id, t, i,
    title: t.title ?? t.action ?? '',
    acted: !!fb?.acted,
    letGo: !!fb?.let_go && !fb?.acted,
    overdue: !!t.due && t.due < localToday() && !fb?.acted && !fb?.let_go,
  }
}))
const open = ref<Set<string>>(new Set())
const unsent = computed(() => rows.value.filter((r) => !props.sent[r.id] && !r.acted && !r.letGo).length)
function toggle(id: string) {
  const s = new Set(open.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  open.value = s
}
watch(() => props.highlight, (id) => { if (id && !open.value.has(id)) toggle(id) })
</script>

<template>
  <div class="head">
    <h3 class="sub">What to do</h3>
    <div class="actions">
      <button v-if="connected && unsent > 1" type="button" class="tt small" :disabled="!!sending" @click="emit('ticktickAll')">
        + Add all {{ unsent }} to TickTick</button>
      <a v-if="!connected" class="small" :href="settingsHref">Connect TickTick</a>
    </div>
  </div>
  <ul class="tasks">
    <li v-for="r in rows" :id="`item-${r.id}`" :key="r.id" :class="{ quiet: r.acted || r.letGo, lit: highlight === r.id }">
      <div class="row">
        <button type="button" class="toggle" :aria-expanded="open.has(r.id)" :aria-controls="`task-${r.id}`" @click="toggle(r.id)">
          <svg class="chev" :class="{ down: open.has(r.id) }" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5" /></svg>
          <span class="title">{{ r.title }}</span>
        </button>
        <span v-if="oneStep === r.i" class="tag">The one thing</span>
        <span v-if="focusItem === r.id" class="badge">Your focus</span>
        <span v-if="r.t.due" class="due" :class="{ late: r.overdue }" :title="r.overdue ? 'Past its date' : 'Do by'"><template v-if="r.overdue">Late · </template>{{ dueDay(r.t.due) }}</span>
        <template v-if="r.acted">
          <span class="done small">✓ Done</span>
          <button type="button" class="link small" @click="emit('change', r.id, { acted: false })">Undo</button>
        </template>
        <template v-else-if="r.letGo">
          <span class="muted small">Let go</span>
          <button type="button" class="link small" @click="emit('change', r.id, { let_go: false })">Undo</button>
        </template>
        <template v-else>
          <a v-if="sent[r.id]" :href="sent[r.id].url" target="_blank" rel="noopener" class="in small" title="Open in TickTick">✓ TickTick ↗</a>
          <button v-else-if="connected" type="button" class="tt small" :disabled="!!sending" @click="emit('ticktick', r.id)">
            {{ sending === r.id ? 'Adding…' : '+ TickTick' }}</button>
          <button v-if="ttDone?.[r.id]" type="button" class="did small" title="Completed in TickTick" @click="emit('change', r.id, { acted: true })">
            Done in TickTick · mark done</button>
          <button v-else type="button" class="did small" @click="emit('change', r.id, { acted: true })">Did it</button>
        </template>
      </div>
      <div v-if="open.has(r.id)" :id="`task-${r.id}`" class="more">
        <p v-if="r.t.detail" class="detail">{{ r.t.detail }}</p>
        <p v-if="r.t.why" class="small muted">Why: {{ r.t.why }}</p>
        <div class="foot">
          <button v-if="focusItem !== r.id && !r.acted" type="button" class="link small" @click="emit('practise', r.id)">Practise this</button>
          <button v-if="!r.acted && !r.letGo" type="button" class="link small" title="Off the list on home; not counted as done"
                  @click="emit('change', r.id, { let_go: true })">Let it go</button>
          <NotRight :feedback="feedback[r.id]" @change="emit('change', r.id, $event)" />
        </div>
      </div>
    </li>
  </ul>
</template>

<style scoped>
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.sub { margin: 0; font-size: 15px; font-weight: 600; }
.actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.tasks { list-style: none; margin: 0; padding: 0; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.tasks li { padding: 4px 12px 4px 6px; border-top: 1px solid var(--line); }
.tasks li:first-child { border-top: 0; }
.tasks li.lit { background: var(--accent-soft); border-radius: 10px; }
.row { display: flex; align-items: center; gap: 10px; min-height: 46px; flex-wrap: wrap; }
.toggle { flex: 1; min-width: 12rem; display: flex; align-items: center; gap: 8px; text-align: left; border: 0; background: none; color: var(--ink);
  font: inherit; padding: 6px; cursor: pointer; border-radius: 8px; }
.toggle:hover { background: var(--quote); }
.chev { width: 16px; height: 16px; flex-shrink: 0; fill: none; stroke: var(--ink-2); stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; transition: transform 0.15s; }
.chev.down { transform: rotate(90deg); }
.title { font-weight: 500; font-size: 15px; }
.quiet .title { color: var(--muted); }
.quiet .title { text-decoration: line-through; text-decoration-color: var(--line-strong); }
.due { font-size: 12px; color: var(--ink-2); border: 1px solid var(--line); border-radius: 999px; padding: 2px 9px; white-space: nowrap; }
.due.late { color: var(--warn); border-color: var(--warn); }
.badge, .tag { font-size: 12px; font-weight: 500; padding: 2px 9px; border-radius: 999px; white-space: nowrap; }
.badge { color: var(--accent); background: var(--accent-soft); }
.tag { color: var(--accent); border: 1px solid var(--accent); }
.done { color: var(--pass); white-space: nowrap; }
.tt, .did { font: inherit; font-size: 13px; border: 1px solid var(--line-strong); background: var(--surface); color: var(--ink-2);
  padding: 5px 12px; min-height: 34px; border-radius: 10px; cursor: pointer; font-weight: 500; white-space: nowrap; }
.did { border-color: var(--accent); color: var(--accent); }
.tt:disabled { opacity: 0.6; cursor: default; }
.in { color: var(--accent); font-weight: 500; text-decoration: none; white-space: nowrap; }
.link { border: 0; background: none; color: var(--accent); cursor: pointer; padding: 6px 0; font: inherit; min-height: 32px; }
.more { padding: 2px 0 12px 32px; display: flex; flex-direction: column; gap: 8px; }
.detail { margin: 0; color: var(--ink-2); }
.foot { display: flex; align-items: center; flex-wrap: wrap; gap: 4px 16px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
p { margin: 0; }
@media (max-width: 640px) {
  .tt, .did, .link { min-height: 44px; }
  .toggle { flex-basis: 100%; }
  .more { padding-left: 8px; }
}
</style>
