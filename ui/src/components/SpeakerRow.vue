<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { lab, type SpeakerAction } from '../lab/api'
import { offerLabel, offerTitle } from '../lab/calendar'
import { mss } from '../lab/format'
import type { Invitee, Speaker, Todo } from '../lab/types'

// canMerge: merging two speakers is precision work, on the Mac only (octo/phone_api.py PHONE_ROUTES).
// canHear: there's audio, and something of theirs to play; hearing: their clip is playing now.
// invitees: from the calendar event this overlaps (B23), offered only; naming with one keeps who that invitee is.
const props = withDefaults(defineProps<{ speaker: Speaker; color: string; sessionId: string; known: string[];
  others: { letter: string; name: string }[]; canMerge?: boolean; canHear?: boolean; hearing?: boolean; invitees?: Invitee[] }>(),
{ canMerge: true, canHear: true, hearing: false, invitees: () => [] })
const emit = defineEmits<{ changed: [result: { speakers: Speaker[]; todo: Todo }]; sample: []; merge: [into: string] }>()

const editing = ref(false)
const draft = ref('')
const busy = ref<'' | 'saving' | 'naming'>('')
const error = ref('')
const input = ref<HTMLInputElement | null>(null)

const sp = computed(() => props.speaker)
const label = computed(() => {
  const s = sp.value
  if (s.name && (s.state === 'confirmed' || s.state === 'auto')) return s.name
  if (s.name && s.state === 'ambiguous') return [s.name, ...s.alternatives].join(' or ') + '?'
  if (s.name) return `${s.name}?`
  return `Speaker ${s.letter}`
})
const candidates = computed(() => (sp.value.state === 'ambiguous' && sp.value.name ? [sp.value.name, ...sp.value.alternatives] : []))

async function act(a: SpeakerAction) {
  busy.value = a.action === 'reject' ? 'saving' : 'naming'  // naming and confirming both save a voiceprint
  error.value = ''
  try {
    emit('changed', await lab.speaker(props.sessionId, sp.value.letter, a))
    editing.value = false
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    busy.value = ''
  }
}
async function startNaming() {
  editing.value = true
  draft.value = ''
  await nextTick()
  input.value?.focus()
}
// On a phone, our own suggestions under the field instead of a datalist: iOS shows a datalist as a menu over
// the page, every known name at once, covering the field and Save, and in the app it hung while a clip played
// (13 Oct). A tap on one names them, as Save would.
const touch = typeof window !== 'undefined' && !!window.matchMedia?.('(pointer: coarse)').matches
const SUGGEST = 6
const suggestions = computed(() => {
  const q = draft.value.trim().toLowerCase()
  if (!touch || !q) return []
  const starts = (n: string) => n.toLowerCase().split(/\s+/).some((w) => w.startsWith(q))
  return props.known.filter(starts).slice(0, SUGGEST)
})
function save() {
  const name = draft.value.trim()
  if (name) void act({ action: 'name', name })
}
</script>

<template>
  <div class="spk">
    <i class="sw" :class="color" aria-hidden="true" />
    <div class="who">
      <div class="strong">{{ label }}<span v-if="sp.self" class="muted"> (you)</span></div>
      <div class="muted small">{{ Math.round(sp.share * 100) }}% of talk · {{ mss(sp.talk_s) }}<template v-if="canHear"> ·
        <button type="button" class="link hear" :aria-pressed="hearing" @click="emit('sample')">{{ hearing ? 'stop' : 'hear them' }}</button></template></div>
    </div>

    <div class="actions">
      <span v-if="busy === 'naming'" class="muted small" role="status">Saving their voiceprint…</span>
      <template v-else-if="editing">
        <label class="sr" :for="`name-${sp.letter}`">Name for speaker {{ sp.letter }}</label>
        <input :id="`name-${sp.letter}`" ref="input" v-model="draft" :list="touch ? undefined : `people-${sp.letter}`" maxlength="40"
               placeholder="Name" autocomplete="off" @keydown.enter.prevent="save" @keydown.esc="editing = false">
        <datalist v-if="!touch" :id="`people-${sp.letter}`"><option v-for="n in known" :key="n" :value="n" /></datalist>
        <button type="button" class="btn btn-small btn-primary" :disabled="!draft.trim()" @click="save">Save</button>
        <button type="button" class="btn btn-small" @click="editing = false">Cancel</button>
        <!-- mousedown.prevent: the field keeps the keyboard, so the page doesn't jump under the finger mid-tap -->
        <div v-if="suggestions.length" class="suggest" role="group" :aria-label="`People named ${draft.trim()}…`">
          <button v-for="n in suggestions" :key="n" type="button" class="btn btn-small" @mousedown.prevent @click="act({ action: 'name', name: n })">{{ n }}</button>
        </div>
      </template>
      <template v-else-if="sp.state === 'confirmed'">
        <span class="ok small">Confirmed</span>
        <button type="button" class="btn btn-small" @click="startNaming">Change</button>
      </template>
      <template v-else-if="sp.state === 'auto' && sp.self">
        <span class="muted small" title="Your voice matched surely enough to go ahead without asking. Confirming saves this voiceprint too.">Auto</span>
        <button type="button" class="btn btn-small" :disabled="!!busy" @click="act({ action: 'confirm' })">Yes, it's me</button>
        <button type="button" class="btn btn-small" :disabled="!!busy" @click="act({ action: 'reject' })">Not me</button>
      </template>
      <template v-else-if="sp.state === 'auto'">
        <span class="muted small" :title="`${sp.name}'s voice matched surely enough to go ahead without asking. Confirming saves this voiceprint too.`">Auto</span>
        <button type="button" class="btn btn-small" :disabled="!!busy" @click="act({ action: 'confirm' })">Yes, {{ sp.name }}</button>
        <button type="button" class="btn btn-small" :disabled="!!busy" @click="act({ action: 'reject' })">Not {{ sp.name }}</button>
      </template>
      <template v-else-if="sp.state === 'suggested'">
        <button type="button" class="btn btn-small btn-primary" :disabled="!!busy" @click="act({ action: 'confirm' })">Yes, {{ sp.name }}</button>
        <button type="button" class="btn btn-small" :disabled="!!busy" @click="act({ action: 'reject' })">Not {{ sp.name }}</button>
      </template>
      <template v-else-if="sp.state === 'ambiguous'">
        <button v-for="n in candidates" :key="n" type="button" class="btn btn-small" :disabled="!!busy" @click="act({ action: 'name', name: n })">{{ n }}</button>
        <button v-for="p in invitees.filter((x) => !candidates.includes(x.name))" :key="p.key" type="button" class="btn btn-small"
                :title="offerTitle(p)" :disabled="!!busy" @click="act({ action: 'name', name: p.name, invitee: p.key })">{{ offerLabel(p) }}</button>
        <button type="button" class="btn btn-small" @click="startNaming">Someone else…</button>
      </template>
      <template v-else>
        <button v-for="p in invitees" :key="p.key" type="button" class="btn btn-small" :title="offerTitle(p)" :disabled="!!busy"
                @click="act({ action: 'name', name: p.name, invitee: p.key })">{{ offerLabel(p) }}</button>
        <button type="button" class="btn btn-small" @click="startNaming">{{ invitees.length ? 'Someone else…' : 'Name…' }}</button>
      </template>
    </div>
    <div v-if="canMerge && !sp.self && others.length && !editing" class="same">
      <label class="muted small" :for="`same-${sp.letter}`">Same person as</label>
      <select :id="`same-${sp.letter}`" class="small" @change="($event.target as HTMLSelectElement).value && emit('merge', ($event.target as HTMLSelectElement).value)">
        <option value="">…</option>
        <option v-for="o in others" :key="o.letter" :value="o.letter">{{ o.name }}</option>
      </select>
    </div>
    <p v-if="error" class="err small" role="alert">{{ error }}</p>
  </div>
</template>

<style scoped>
.spk { display: grid; grid-template-columns: 12px minmax(0, 1fr) auto; gap: 8px 12px; align-items: center; padding: 10px 0; border-top: 1px solid var(--line); }
.sw { width: 12px; height: 12px; border-radius: 3px; }
.sw { background: var(--c); }
.strong { font-weight: 600; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.ok { color: var(--pass); }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
.actions input { font: inherit; font-size: 14px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 6px 10px; height: 36px; width: 160px; }
.btn-small { height: 36px; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font-size: 13px; }
/* On a phone, a bigger place to tap than the text itself, without moving the line (B1 design check, 13 Oct). */
@media (pointer: coarse) { .hear { padding: 12px 8px; margin: -12px -8px; } }
.same { grid-column: 2 / -1; display: flex; align-items: center; gap: 8px; }
.same select { font: inherit; font-size: 13px; color: var(--ink); background: var(--ground); border: 1px solid var(--line); border-radius: 8px; padding: 4px 8px; height: 32px; }
.suggest { flex-basis: 100%; display: flex; gap: 8px; flex-wrap: wrap; }
.err { grid-column: 2 / -1; margin: 0; color: var(--fail); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 640px) {
  .actions { grid-column: 2 / -1; justify-content: flex-start; }
  .actions :is(button, .btn-small), .same select { min-height: 44px; height: auto; }
}
</style>
