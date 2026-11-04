<script setup lang="ts">
// Home: the coach in two lines (docs/coach-plan.md §4.3). What its latest note says to work on and
// a way to open it; or a way to ask, while it reads, or why it can't yet. The note itself is on the
// Coach page, so home stays a list of what to do.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { day } from '../lab/format'
import { href } from '../lab/router'
import type { CoachSummary } from '../lab/types'
import FoldToggle from './FoldToggle.vue'

const c = ref<CoachSummary | null>(null)
const asking = ref(false)
const open = ref(true)
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined
async function load() {
  try {
    c.value = await lab.coach()
    error.value = ''
  } catch { /* home works without it */ }
  if (!c.value?.running && timer) { clearInterval(timer); timer = undefined }
}
function poll() { if (!timer) timer = setInterval(load, 5000) }
onMounted(async () => { await load(); if (c.value?.running) poll() })
onUnmounted(() => { if (timer) clearInterval(timer) })

async function ask() {
  asking.value = true
  try { c.value = await lab.askCoach(); error.value = ''; poll() } catch (e) { error.value = e instanceof Error ? e.message : String(e) } finally { asking.value = false }
}
const latest = computed(() => c.value?.latest ?? null)
const work = computed(() => latest.value?.note?.work_on ?? null)
const reviews = (n: number) => `${n} review${n === 1 ? '' : 's'}`
</script>

<template>
  <section v-if="c && (c.latest || c.running || c.failed || c.conversations > 0)" class="card coach" aria-labelledby="coach-h">
    <div class="grow">
      <div id="coach-h" class="eyebrow eyebrow-c">The coach<template v-if="latest"> · {{ day(latest.created_at) }}</template>
        <span v-if="latest && !latest.seen" class="badge">New</span></div>
      <template v-if="c.running">
        <div class="text">Reading your {{ reviews(c.reads) }} together…</div>
        <div v-if="open" class="muted small" role="status">A minute or two. You can leave this page; it carries on.</div>
      </template>
      <template v-else-if="latest">
        <a class="text link-text" :href="href.coach">{{ work ? `Work on: ${work.theme}` : 'Nothing held up across two conversations yet' }}</a>
        <div v-if="open && work?.try" class="muted small">Try: {{ work.focus_text }}</div>
      </template>
      <template v-else>
        <div class="text">What would a coach tell you to work on?</div>
        <div v-if="open" class="muted small">{{ c.can_ask ? `It reads your ${reviews(c.reads)} together, with what you marked, and names one thing. One Claude call.`
          : c.why_not && c.why_not.startsWith('the coach needs') ? `Once two conversations have reviews, it reads them together and names one thing to work on.` : c.why_not }}</div>
      </template>
      <p v-if="c.failed && !c.running" class="error small" role="alert">The coach couldn't finish: {{ c.failed.error }}</p>
      <p v-if="error" class="error small" role="alert">{{ error }}</p>
    </div>
    <FoldToggle v-model="open" name="coach" label="the coach" />
    <div v-if="open" class="acts">
      <a v-if="latest && !c.running" class="btn btn-small" :href="href.coach">Open the note</a>
      <button v-if="!c.running && (!latest || c.new_since > 0 || c.failed)" type="button" class="btn btn-small"
              :class="{ 'btn-primary': !latest }" :disabled="!c.can_ask || asking"
              :title="c.can_ask ? undefined : c.why_not ?? undefined" @click="ask">{{ asking ? 'Asking…' : latest || c.failed ? 'Ask again' : 'Ask the coach' }}</button>
      <span v-if="latest && c.new_since > 0 && !c.running" class="muted small since">{{ reviews(c.new_since) }} since this note</span>
    </div>
  </section>
</template>

<style scoped>
.coach { padding: 14px 18px; display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.grow { flex: 1; min-width: 240px; display: flex; flex-direction: column; gap: 3px; }
.eyebrow-c { font-size: 13px; color: var(--accent); font-weight: 500; display: flex; align-items: center; gap: 8px; }
.badge { text-transform: none; letter-spacing: 0; }
a.btn { color: var(--ink); text-decoration: none; }
.text { font-size: 16px; font-weight: 600; color: var(--ink); }
.link-text { text-decoration: none; }
.link-text:hover { text-decoration: underline; text-underline-offset: 3px; }
.coach .fold { align-self: flex-start; margin-top: -4px; order: 1; }  /* after the buttons; on a phone, beside the eyebrow */
.acts { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.since { flex-basis: 100%; text-align: right; }
.badge { font-size: 12px; font-weight: 500; color: var(--accent); background: var(--accent-soft); padding: 1px 8px; border-radius: 999px; }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); margin: 0; }
@media (max-width: 640px) {
  .acts { flex-basis: 100%; }
  .acts .btn { flex: 1; justify-content: center; height: 44px; }
  .since { text-align: left; }
  .coach .fold { order: 0; }
}
</style>
