<script setup lang="ts">
// Talk it through (B31, octo/talk.py): after its review, the owner asks about the conversation in their own words and
// the review's model answers from its lines. One thread per conversation, kept; each reply runs in the background on
// the Mac (a minute or two), so the page polls while it's written.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import StatusIcon from './StatusIcon.vue'
import { lab } from '../lab/api'
import { mss, when } from '../lab/format'
import { canDelete } from '../lab/where'
import type { Talk } from '../lab/types'

const props = defineProps<{ sessionId: string }>()
const emit = defineEmits<{ open: [line: number] }>()
const t = ref<Talk | null>(null)
const text = ref('')
const busy = ref(false)
const error = ref('')
const thread = ref<HTMLElement | null>(null)
let timer: number | undefined

const fail = (e: unknown) => { error.value = e instanceof Error ? e.message : String(e) }
const mine = computed(() => t.value?.turns.filter((x) => x.by === 'me').length ?? 0)
const unanswered = computed(() => !!t.value && !t.value.running && t.value.turns.at(-1)?.by === 'me')
const blocked = computed(() => busy.value || !t.value || t.value.running || !!t.value.why_not || unanswered.value || mine.value >= (t.value?.max_turns ?? 0))
const scrollDown = () => nextTick(() => thread.value?.lastElementChild?.scrollIntoView({ block: 'nearest' }))

function poll() {
  window.clearTimeout(timer)
  if (t.value?.running) timer = window.setTimeout(() => { void load() }, 3000)
}
async function load() {
  try {
    const was = t.value?.running
    t.value = await lab.talk(props.sessionId)
    if (was && !t.value.running) void scrollDown()
  } catch (e) { fail(e) }
  poll()
}
watch(() => props.sessionId, () => { t.value = null; error.value = ''; void load() }, { immediate: true })
onBeforeUnmount(() => window.clearTimeout(timer))

async function send(message: string) {
  if (!t.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    t.value = await lab.sayTalk(props.sessionId, message)
    text.value = ''
    void scrollDown()
  } catch (e) { fail(e) } finally { busy.value = false }
  poll()
}
async function startOver() {
  if (!confirm('Start over? This conversation\'s thread is deleted.')) return
  try { t.value = await lab.clearTalk(props.sessionId) } catch (e) { fail(e) }
}
function key(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    if (text.value.trim() && !blocked.value) void send(text.value)
  }
}
const paras = (s: string) => s.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean)
</script>

<template>
  <section class="card talk" aria-labelledby="talk-h">
    <div class="row-head">
      <h2 id="talk-h">Talk it through</h2>
      <button v-if="t?.turns.length && canDelete" type="button" class="link small" :disabled="t.running" @click="startOver">Start over</button>
    </div>
    <p v-if="!t?.turns.length" class="small muted">Ask anything about this conversation: what someone meant, how it went, how you could
      have said something. The answers come from its lines and its review, and are held to your principles.</p>
    <ol v-if="t?.turns.length" ref="thread" class="thread" aria-live="polite">
      <li v-for="(x, i) in t.turns" :key="i" :class="x.by">
        <div class="bubble"><p v-for="(p, k) in paras(x.text)" :key="k">{{ p }}</p></div>
        <ul v-if="x.moments?.length" class="moments">
          <li v-for="m in x.moments" :key="m.line"><button type="button" class="link small" @click="emit('open', m.line)">{{ mss(m.start) }}
            {{ m.who }}<template v-if="m.uncertain"> ⚠</template></button><span class="small"> “{{ m.text }}”</span></li>
        </ul>
        <p v-if="x.dropped" class="small muted">{{ x.dropped }} moment{{ x.dropped === 1 ? '' : 's' }} it gave weren't in their lines: left out.</p>
      </li>
      <li v-if="t.running" class="model waiting" role="status"><StatusIcon state="running" :size="18" />
        <span class="small muted">Reading the conversation and writing a reply: usually a minute or two. You can leave this page; it carries on.</span></li>
    </ol>
    <p v-if="unanswered" class="small">
      <span class="muted">{{ t?.waiting_until ? `Waiting for the usage limit to reset (${when(t.waiting_until)}).`
        : t?.error ? `No reply: ${t.error}` : 'No reply to your last message yet.' }}</span>
      <button type="button" class="btn btn-small" :disabled="busy || !!t?.why_not" @click="send('')">Ask again</button></p>
    <form v-if="t && (t.turns.length || !t.why_not)" class="say" @submit.prevent="text.trim() && send(text)">
      <label class="sr" for="talk-box">Your message</label>
      <textarea id="talk-box" v-model="text" rows="2" :maxlength="t.max_line" :disabled="mine >= t.max_turns"
                :placeholder="t.turns.length ? 'Ask a follow-up' : 'e.g. What did their answer to my question actually mean?'" @keydown="key" />
      <div class="actions">
        <button type="submit" class="btn btn-primary" :disabled="!text.trim() || blocked">
          <StatusIcon v-if="busy" state="running" :size="16" />{{ busy ? 'Sending…' : 'Ask' }}</button>
        <span class="small muted">Enter sends. Each reply is one call to your review's model ({{ mine }} of {{ t.max_turns }}).</span>
      </div>
    </form>
    <p v-if="t?.why_not" class="small muted">Not now: {{ t.why_not }}.</p>
    <p v-if="error" class="small error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.talk { padding: 22px 24px; display: flex; flex-direction: column; gap: 12px; }
.row-head { display: flex; align-items: baseline; gap: 12px; }
h2 { margin: 0; font-size: 19px; font-weight: 600; flex-grow: 1; }
p { margin: 0; }
.thread { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.thread > li { display: flex; flex-direction: column; gap: 4px; max-width: 88%; }
.bubble { padding: 8px 12px; border-radius: 12px; line-height: 1.5; display: flex; flex-direction: column; gap: 8px;
  white-space: pre-wrap; overflow-wrap: anywhere; }
.me { align-self: flex-end; align-items: flex-end; }
.me .bubble { background: var(--accent-soft); color: var(--ink); }
.model .bubble { background: var(--quote); }
.waiting { flex-direction: row; align-items: center; gap: 8px; }
.moments { list-style: none; margin: 0; padding: 0 0 0 12px; display: flex; flex-direction: column; gap: 2px; color: var(--ink-2); }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; min-height: 36px; }
.link:disabled { color: var(--muted); cursor: default; }
.moments .link { min-height: 28px; }
.say { display: flex; flex-direction: column; gap: 8px; }
.say textarea { font: inherit; font-size: 15px; color: var(--ink); background: var(--ground); border: 1px solid var(--line);
  border-radius: 9px; padding: 8px 10px; min-height: 40px; width: 100%; box-sizing: border-box; resize: vertical; line-height: 1.4; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.actions .btn { display: inline-flex; align-items: center; gap: 6px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
@media (max-width: 640px) {
  .talk { padding: 18px 16px; }
  .thread > li { max-width: 94%; }
  .btn, .link { min-height: 44px; }
  .say textarea { min-height: 44px; font-size: 16px; }  /* 16 px: iOS doesn't zoom into the field */
}
</style>
