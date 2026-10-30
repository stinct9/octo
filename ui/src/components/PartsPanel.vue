<script setup lang="ts">
import { computed, ref } from 'vue'
import { lab } from '../lab/api'
import { mss, when } from '../lab/format'
import { href } from '../lab/router'
import type { Part, PartOffer, SessionDetail } from '../lab/types'

// One discussion in parts (B35, octo/parts.py): each part keeps its own recording and transcript; one review reads them
// all, on the first. The parts, who grouped them and why, Not one discussion; or, alone, the neighbours to group with.
// Grouping and ungrouping are the Mac's, like split.
const props = defineProps<{ s: SessionDetail; canEdit: boolean }>()
const emit = defineEmits<{ changed: [] }>()

const p = computed(() => props.s.parts)
const list = computed(() => p.value?.list ?? [])
const head = computed(() => p.value?.head ?? props.s.id)
const offers = computed(() => (props.canEdit ? p.value?.offers ?? [] : []))
const asking = ref<PartOffer | null>(null)
const busy = ref(false)
const error = ref('')

const gap = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`)
const people = (who: string[]) => (who.length ? `with ${who.slice(0, 3).join(', ')}${who.length > 3 ? '…' : ''}` : 'nobody else named')
const WHY: Record<string, string> = {
  hour: 'the hour limit cut it there, and the same people carried on',
  pause: 'the same people carried on after the pause',
  recordings: 'the same people carried on in the next recording',
  joined: 'joined before parts were read as one',
}
const why = (x: Part) => (x.by === 'lab' ? `The lab put this with the part before: ${WHY[x.kind ?? ''] ?? 'the same people carried on'}.` : 'You put this with the part before.')

async function act(run: () => Promise<{ session: string }>) {
  busy.value = true
  error.value = ''
  try {
    const r = await run()
    asking.value = null
    if (r.session !== props.s.id) location.hash = href.conversation(r.session)
    else emit('changed')
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { busy.value = false }
}
const group = (o: PartOffer) => act(() => lab.group(props.s.id, o.side))
const ungroup = (x: Part) => act(() => lab.ungroup(props.s.id, x.first ?? '', x.id))
const redo = () => act(async () => { await lab.redoReview(head.value); return { session: props.s.id } })
</script>

<template>
  <section v-if="list.length" class="parts small" aria-label="One discussion in parts">
    <p v-if="s.id !== head" role="note"><strong>Part {{ list.find((x) => x.id === s.id)?.n }} of one discussion.</strong>
      Its review reads every part: <a :href="href.conversation(head)">open the whole</a>.</p>
    <p v-else role="note"><strong>One discussion in {{ list.length }} parts</strong>, recorded apart and read as one.</p>
    <ol>
      <li v-for="x in list" :key="x.id">
        <span v-if="x.gap_s != null" class="muted">… {{ gap(x.gap_s) }} later … </span>
        <a v-if="x.id !== s.id" :href="href.conversation(x.id)">Part {{ x.n }}</a><strong v-else>Part {{ x.n }}</strong>
        · {{ when(x.at) }} · {{ mss(x.duration_s) }}<template v-if="x.answered"> · answered</template>
        <template v-if="x.first">
          <br><span class="muted">{{ why(x) }}</span>
          <template v-if="canEdit"> <button type="button" class="link" :disabled="busy" @click="ungroup(x)">Not one discussion</button></template>
        </template>
      </li>
    </ol>
    <p v-if="s.todo.part && s.todo.part_id" class="warn" role="note">The review of the whole waits on
      <a :href="href.conversation(s.todo.part_id)">part {{ s.todo.part }}</a>: open it to {{ s.todo.kind === 'confirm' ? "confirm who's who" : 'see where it is' }}.</p>
    <p v-if="p?.held && s.id === head && !s.review_outdated" role="note">You'd answered a part before they were put together, so each part keeps
      its own review and its answers. <button type="button" class="link" :disabled="busy" @click="redo">Redo the review</button>
      to have the whole discussion read as one; the answers you gave stay and count.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
  <section v-else-if="offers.length" class="parts small muted" aria-label="One discussion with a neighbour">
    <p v-if="!asking">
      <template v-for="(o, i) in offers" :key="o.side"><template v-if="i"> · </template><button type="button" class="link"
        @click="asking = o">One discussion with the one {{ o.side }}</button> ({{ gap(o.gap_s) }} apart, {{ o.minutes }} min,
        {{ people(o.who) }}<template v-if="o.ungrouped">; you said they weren't</template>)</template>
    </p>
    <p v-else class="ask" role="alertdialog">
      Read this and the one {{ asking.side }} it as one discussion? Each keeps its recording and transcript; one review reads
      both, and the list shows them once. Answers you gave on either stay.
      <button type="button" :disabled="busy" @click="group(asking)">Put them together</button>
      <button type="button" class="link" :disabled="busy" @click="asking = null">Cancel</button>
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.parts { margin: 0 0 12px; color: var(--muted); }
.parts p { margin: 0 0 4px; }
.parts strong, .ask { color: var(--ink); }
.parts ol { margin: 4px 0 6px; padding-left: 20px; color: var(--ink); }
.parts li { margin: 0 0 4px; }
.warn { color: var(--warn); }
.ask button { margin-left: 6px; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
@media (pointer: coarse) { p .link, li .link { padding: 12px 0; margin: -12px 0; } }
</style>
