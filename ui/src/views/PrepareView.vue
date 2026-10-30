<script setup lang="ts">
// Prepare for a conversation (B36, octo/prepare.py): a plan for the next conversation with someone, from their
// conversations and the lines naming them in others'. The owner sees what would go, unticks mentions that aren't them,
// then one call makes the plan; every moment in it is checked and opens its conversation at that line. Read top down
// for the minutes before a meeting: the aim, what to close, what to be careful with, what to ask; the evidence after.
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { lab } from '../lab/api'
import { day, when } from '../lab/format'
import { marked, sendsText, spellings } from '../lab/prepare'
import { href } from '../lab/router'
import type { PreparePlan, PreparePreview, PrepareState } from '../lab/types'

const props = defineProps<{ id: string }>()
const st = ref<PrepareState | null>(null)
const pick = ref<PreparePreview | null>(null)
const about = ref('')
const names = ref('')
const skip = ref<Set<string>>(new Set())
const making = ref(false)  // the form is open: no plan yet, or "Make a new one"
const busy = ref(false)
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined

const fail = (e: unknown) => { error.value = e instanceof Error ? e.message : String(e) }
const focus = (id: string) => nextTick(() => document.getElementById(id)?.focus())
async function load() {
  const was = st.value?.plan?.status
  try { st.value = await lab.prepare(props.id); error.value = '' } catch (e) { fail(e) }
  const now = st.value?.plan?.status
  if (now === 'running' && !timer) timer = setInterval(load, 3000)
  else if (now !== 'running' && timer) { clearInterval(timer); timer = undefined }
  if (was === 'running' && now === 'done') focus('a-h')
  if (st.value && !st.value.plan && !making.value) open()
}
async function refresh() {
  try { pick.value = await lab.preparePick(props.id, spellings(names.value), [...skip.value]) } catch (e) { fail(e) }
}
function open() {
  making.value = true
  const last = st.value?.plan ?? null
  about.value = last?.about || st.value?.event?.title || ''
  names.value = (last?.names ?? []).join(', ')
  skip.value = new Set((last?.sent?.mentions ?? []).filter((m) => !m.ticked).map((m) => m.session_id))
  refresh()
  focus('f-h')
}
function tick(sid: string, on: boolean) {
  const s = new Set(skip.value)
  if (on) s.delete(sid); else s.add(sid)
  skip.value = s
  refresh()
}
function tickAll(on: boolean) {
  skip.value = on ? new Set() : new Set((pick.value?.mentions ?? []).map((m) => m.session_id))
  refresh()
}
onMounted(load)
onUnmounted(() => { if (timer) clearInterval(timer) })

async function prepare() {
  busy.value = true
  try {
    const ticked = (pick.value?.mentions ?? []).filter((m) => m.ticked).map((m) => m.session_id)
    st.value = await lab.startPrepare(props.id, { about: about.value, names: spellings(names.value), mentions: ticked })
    making.value = false
    await load()
    focus('run-h')
  } catch (e) { fail(e) } finally { busy.value = false }
}
async function remove() {
  if (!confirm('Delete this plan? You can make a new one.')) return
  busy.value = true
  try { st.value = await lab.deletePrepare(props.id); open() } catch (e) { fail(e) } finally { busy.value = false }
}
async function saveIntention(aim: string) {
  try { await lab.intention(props.id, aim); if (st.value) st.value.intention = aim } catch (e) { fail(e) }
}

const name = computed(() => st.value?.person.name ?? '')
const plan = computed(() => st.value?.plan ?? null)
// What to read: the newest plan once done; while a new one runs or after it failed, the one made before.
const shown = computed<PreparePlan | null>(() => (plan.value?.status === 'done' ? plan.value : st.value?.previous ?? null))
const body = computed(() => shown.value?.plan ?? null)
const minutes = computed(() => (body.value?.structure ?? []).reduce((a, s) => a + s.minutes, 0))
const nothing = computed(() => !!pick.value && !pick.value.lines)
const thin = computed(() => !!pick.value && pick.value.lines > 0 && pick.value.lines < 20)
const allTicked = computed(() => (pick.value?.mentions ?? []).every((m) => m.ticked))
const plural = (k: number, one: string, many: string) => (k === 1 ? one : many)
</script>

<template>
  <main class="page">
    <a :href="href.person(id)" class="back">← {{ name || 'Their page' }}</a>
    <h1>Prepare for a conversation<template v-if="name"> with {{ name }}</template></h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!st && !error" class="muted loading" role="status">Loading…</p>

    <template v-if="st">
      <!-- the form: what it would read, before anything is sent -->
      <section v-if="making" class="card pad" aria-labelledby="f-h">
        <h2 id="f-h" tabindex="-1">What it reads</h2>
        <p class="lead">Your conversations with {{ name }}, and the lines naming them in conversations they weren't in, with
          3 lines either side. Nothing leaves this Mac until you press Prepare. Then these lines, what your reviews read in
          {{ name }} and your principles go to your review's model. Some are other people's words about {{ name }}.</p>
        <label class="field">
          <span class="small">What this conversation is <span class="muted">(optional)</span></span>
          <input v-model="about" type="text" :maxlength="st.max_about" placeholder="First 1:1, an introduction after the reorg">
        </label>
        <label class="field">
          <span class="small">Other spellings of their name <span class="muted">(optional, commas between)</span></span>
          <input v-model="names" type="text" placeholder="Tam, Tami" @change="refresh">
        </label>
        <p v-if="!pick" class="muted small loading" role="status">Looking…</p>
        <template v-else>
          <p v-if="pick.short_first" class="warn small">“{{ pick.short_first }}” is too short to search for on its own: only
            {{ name }}'s whole name and the spellings you add are looked for.</p>
          <p v-if="pick.shared_first.length" class="warn small">Someone else in People has the same first name
            ({{ pick.shared_first.join(', ') }}): untick the mentions that are about them.</p>
          <h3 class="sub">With {{ name }} <span class="muted small">· {{ pick.with.length }}</span></h3>
          <p v-if="!pick.with.length" class="muted small">No conversation with {{ name }} confirmed yet.</p>
          <ul v-else class="convs">
            <li v-for="c in pick.with" :key="c.session_id">
              <a :href="href.conversation(c.session_id)">{{ c.at ? when(c.at) : 'Date unknown' }}</a>
              <span class="muted small"> · {{ c.lines ? `${c.lines} lines${c.whole ? ', all of it' : ''}` : "doesn't fit" }}</span>
              <details v-if="c.going?.length" class="going"><summary class="small">The lines that go</summary>
                <p v-for="g in c.going" :key="g.line" class="small"><span class="who">{{ g.who }}</span> {{ g.text }}</p></details>
            </li>
          </ul>
          <div class="head">
            <h3 class="sub">Naming {{ name }} <span class="muted small">· {{ pick.mentions.length }}</span></h3>
            <button v-if="pick.mentions.length > 1" type="button" class="link small" @click="tickAll(!allTicked)">
              {{ allTicked ? 'Untick all' : 'Tick all' }}</button>
          </div>
          <p v-if="!pick.mentions.length" class="muted small">No other conversation names them.</p>
          <ul v-else class="mentions">
            <li v-for="m in pick.mentions" :key="m.session_id" :class="{ off: !m.ticked }">
              <label class="tick">
                <input type="checkbox" :checked="m.ticked" @change="tick(m.session_id, ($event.target as HTMLInputElement).checked)">
                <span class="tick-body">
                  <span>{{ m.at ? when(m.at) : 'Date unknown' }}<template v-if="m.with.length"> · {{ m.with.join(', ') }} there</template>
                    <span class="muted small"> · named {{ m.hits }} {{ plural(m.hits, 'time', 'times') }}</span></span>
                  <span v-for="s in m.shown" :key="s.line" class="said"><span class="who">{{ s.who }}</span>
                    “<template v-for="(part, k) in marked(s.text, pick.names)" :key="k"><mark v-if="part.hit">{{ part.text }}</mark><template
                      v-else>{{ part.text }}</template></template>”</span>
                </span>
              </label>
              <a class="hear small" :href="href.line(m.session_id, m.shown[0]?.line ?? null)">Hear it</a>
              <details v-if="m.ticked && m.going?.length" class="going full"><summary class="small">All {{ m.going.length }} lines that go</summary>
                <p v-for="g in m.going" :key="g.line" class="small"><span class="who">{{ g.who }}</span> {{ g.text }}</p></details>
            </li>
          </ul>
        </template>
        <div class="bar">
          <p v-if="pick" class="small muted">{{ sendsText(pick) }}.<template v-if="thin"> Only a few lines: the plan will be thin.</template></p>
          <div class="acts">
            <button type="button" class="btn btn-primary" :disabled="busy || !pick || nothing || !!st.why_not" @click="prepare">
              {{ busy ? 'Starting…' : shown ? 'Replace the plan' : 'Prepare' }}</button>
            <button v-if="plan" type="button" class="btn" :disabled="busy" @click="making = false">Cancel</button>
          </div>
          <p v-if="shown" class="muted small">The plan you have stays until the new one is made.</p>
          <p v-if="st.why_not" class="muted small">Can't prepare now: {{ st.why_not }}.</p>
        </div>
      </section>

      <template v-else-if="plan">
        <div v-if="plan.status === 'running'" class="card pad status" role="status">
          <span class="dots" aria-hidden="true"><i /><i /><i /></span>
          <span id="run-h" tabindex="-1">Making the plan… a minute or two. This page updates by itself.</span>
        </div>
        <section v-else-if="plan.status === 'failed'" class="card pad">
          <p class="error" role="alert">Couldn't make the plan: {{ plan.error }}</p>
          <p v-if="plan.waiting_until" class="muted small">It can be asked again after {{ when(plan.waiting_until) }}.</p>
          <p v-if="shown" class="muted small">The one you made before is below.</p>
          <div class="acts"><button type="button" class="btn btn-primary" @click="open">Try again</button></div>
        </section>

        <template v-if="shown && body">
          <section class="card pad aim" aria-labelledby="a-h">
            <p v-if="shown.used" class="used small">This plan was for your conversation
              <template v-if="shown.used.session_id">on <a :href="href.conversation(shown.used.session_id)">{{ when(shown.used.at) }}</a>, and shows beside its review</template><template
                v-else>at {{ when(shown.used.at) }}</template>. Make a new one for the next.</p>
            <h2 id="a-h" class="eyebrow" tabindex="-1">Your aim</h2>
            <p class="big">{{ body.aim }}</p>
            <div class="acts">
              <button type="button" class="btn btn-small" :disabled="st.intention === body.aim" @click="saveIntention(body.aim)">
                {{ st.intention === body.aim ? 'Your intention' : 'Save as my intention' }}</button>
              <span v-if="st.intention && st.intention !== body.aim" class="muted small">Replaces: “{{ st.intention }}”</span>
            </div>
            <p class="muted small">Made {{ when(shown.at) }}<template v-if="shown.event"> for “{{ shown.event }}”</template><template
              v-else-if="shown.about"> · “{{ shown.about }}”</template></p>
            <p v-if="body.why" class="prose">{{ body.why }}</p>
          </section>

          <section v-if="st.close_first.length" class="card pad" aria-labelledby="c-h">
            <h2 id="c-h">Close first</h2>
            <ul class="plain">
              <li v-for="(c, i) in st.close_first" :key="i">
                <a :href="href.conversation(c.session_id)">{{ c.text }}</a>
                <span class="muted small"> · {{ c.kind === 'step' ? 'a step from a review' : c.mine ? 'you promised' : `${name} promised` }}<template
                  v-if="c.due"> · due {{ day(new Date(c.due).getTime() / 1000) }}</template></span>
              </li>
            </ul>
          </section>

          <section v-if="body.careful.length" class="card pad careful" aria-labelledby="k-h">
            <h2 id="k-h">Be careful</h2>
            <article v-for="(k, i) in body.careful" :key="i" class="item">
              <p class="prose">{{ k.text }}</p>
              <a v-for="(q, j) in k.quotes" :key="j" class="moment" :href="href.line(q.session_id, q.line)">
                <span class="who">{{ q.who }}</span><span class="muted small">{{ q.at ? when(q.at) : '' }} · hear it</span>
                <span class="text">“{{ q.text }}”</span></a>
            </article>
          </section>

          <section v-if="body.questions.length" class="card pad" aria-labelledby="q-h">
            <h2 id="q-h">Questions to ask</h2>
            <div v-for="(g, i) in body.questions" :key="i" class="group">
              <h3 class="sub">{{ g.purpose }}</h3>
              <ul class="plain qs"><li v-for="(q, j) in g.items" :key="j">{{ q }}</li></ul>
            </div>
          </section>

          <section v-if="body.structure.length" class="card pad" aria-labelledby="s-h">
            <div class="head"><h2 id="s-h">How it could go</h2><span class="muted small">about {{ minutes }} minutes</span></div>
            <ol class="steps">
              <li v-for="(s, i) in body.structure" :key="i"><span class="mins small">{{ s.minutes }} min</span>
                <span><strong>{{ s.part }}</strong> {{ s.detail }}</span></li>
            </ol>
          </section>

          <section v-for="sec in [{ id: 't', title: `How ${name} seems to think`, items: body.thinking }, { id: 'o', title: 'What\'s open around them', items: body.open }].filter((s) => s.items.length)"
                   :key="sec.id" class="card pad" :aria-labelledby="`${sec.id}-h`">
            <h2 :id="`${sec.id}-h`">{{ sec.title }}</h2>
            <article v-for="(it, i) in sec.items" :key="i" class="item">
              <h3>{{ it.headline }}<span v-if="it.second_hand" class="flag" title="From what others said, not their own words">second-hand</span></h3>
              <p class="prose">{{ it.observation }}</p>
              <a v-for="(q, j) in it.quotes" :key="j" class="moment" :href="href.line(q.session_id, q.line)">
                <span class="who" :class="{ me: q.self }">{{ q.who }}<template v-if="q.uncertain"> (unsure)</template></span>
                <span class="muted small">{{ q.at ? when(q.at) : '' }} · hear it</span>
                <span class="text">“{{ q.text }}”</span>
              </a>
            </article>
          </section>

          <p v-if="body.withheld" class="muted small">{{ body.withheld }} {{ plural(body.withheld, 'point rested', 'points rested') }}
            on something said in confidence, so {{ plural(body.withheld, "it's", "they're") }} left out.</p>
          <p v-if="body.unbacked" class="muted small">{{ body.unbacked }} {{ plural(body.unbacked, 'reading had', 'readings had') }}
            no moment the lab could check, so {{ plural(body.unbacked, "it's", "they're") }} left out.</p>
          <p v-if="body.dropped" class="muted small">{{ body.dropped }} {{ plural(body.dropped, "quote wasn't", "quotes weren't") }}
            in what was sent, so {{ plural(body.dropped, "it's", "they're") }} left out.</p>
          <p v-if="body.limits" class="muted small">{{ body.limits }}</p>

          <details v-if="shown.sent" class="fold">
            <summary class="small">Show what it read <span class="muted">· {{ sendsText(shown.sent, true) }}</span></summary>
            <ul class="plain sent">
              <li v-for="c in [...shown.sent.with, ...shown.sent.mentions].filter((c) => c.lines)" :key="c.session_id">
                <a :href="href.conversation(c.session_id)">{{ c.at ? when(c.at) : 'Date unknown' }}<template v-if="c.with.length"> · with {{ c.with.join(', ') }}</template></a>
                <span class="muted small"> · {{ c.lines }} lines</span></li>
            </ul>
          </details>
        </template>

        <div v-if="plan.status !== 'running'" class="acts foot">
          <button type="button" class="btn btn-small" :disabled="busy" @click="open">Make a new one</button>
          <span class="muted small">Ready until your next conversation with {{ name }}<template v-if="shown?.event"> or the meeting
            it was made for</template>, then beside its review for a week. Deleted with any conversation it read.</span>
          <button type="button" class="link small" :disabled="busy" @click="remove">Delete</button>
        </div>
      </template>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 820px; margin: 0 auto; padding: 20px 24px 56px; display: flex; flex-direction: column; gap: 16px; }
.back { font-size: 14px; text-decoration: none; align-self: flex-start; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
h2 { margin: 0; font-size: 17px; font-weight: 600; }
h2:focus, span:focus { outline: none; }
h3 { margin: 0; font-size: 15px; font-weight: 600; line-height: 1.4; }
.sub { margin-top: 4px; font-size: 14px; }
p { margin: 0; }
.lead, .prose { color: var(--ink-2); }
.pad { padding: 16px 18px; display: flex; flex-direction: column; gap: 12px; }
.head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.eyebrow { font-size: 13px; color: var(--accent); font-weight: 500; }
.big { font-size: 18px; font-weight: 500; line-height: 1.45; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field input { min-height: 44px; padding: 0 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface);
  color: var(--ink); font: inherit; font-size: 15px; }
.convs, .mentions, .plain { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.mentions li { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 4px 12px; padding: 8px 0; border-top: 1px solid var(--line); }
.going summary { cursor: pointer; padding: 8px 0; color: var(--accent); }
.going p { margin: 2px 0; color: var(--ink-2); overflow-wrap: anywhere; }
.going.full { flex-basis: 100%; padding-left: 28px; }
.used { padding: 8px 12px; border-radius: 10px; background: var(--quote); color: var(--ink-2); }
.mentions li.off .said { opacity: 0.5; }
.tick { flex: 1; min-width: 0; display: flex; gap: 10px; align-items: flex-start; min-height: 44px; cursor: pointer; }
.tick input { width: 18px; height: 18px; flex: none; margin-top: 3px; }
.tick-body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.said { font-size: 14px; color: var(--ink-2); overflow-wrap: anywhere;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
mark { background: var(--accent-soft); color: var(--ink); border-radius: 3px; padding: 0 2px; }
.hear { flex: none; display: inline-block; padding: 12px 0; margin: -6px 0; }
.who { font-weight: 500; }
.who.me { color: var(--me); }
.bar { position: sticky; bottom: 0; display: flex; flex-direction: column; gap: 8px; padding: 12px 0 4px;
  background: var(--surface); border-top: 1px solid var(--line); }
.item { display: flex; flex-direction: column; gap: 6px; }
.item + .item { padding-top: 12px; border-top: 1px solid var(--line); }
.flag { margin-left: 8px; padding: 1px 8px; border-radius: 999px; background: var(--quote); color: var(--muted); font-size: 12px;
  font-weight: 500; white-space: nowrap; display: inline-block; }
.moment { display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: baseline; padding: 8px 12px; border-radius: 10px;
  background: var(--quote); color: inherit; text-decoration: none; min-height: 44px; }
.moment:hover { background: var(--accent-soft); }
.moment .text { flex-basis: 100%; font-size: 14px; overflow-wrap: anywhere; }
.steps { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 10px; }
.steps li { display: flex; gap: 12px; align-items: baseline; }
.mins { flex: none; width: 52px; color: var(--muted); font-variant-numeric: tabular-nums; }
.qs li { padding-left: 14px; position: relative; }
.qs li::before { content: '–'; position: absolute; left: 0; color: var(--muted); }
.group { display: flex; flex-direction: column; gap: 6px; }
.status { flex-direction: row; align-items: center; color: var(--ink-2); }
.acts { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.foot .muted { flex: 1; min-width: 200px; }
.link { background: none; border: 0; padding: 12px 0; margin: -12px 0; color: var(--accent); cursor: pointer; font: inherit; }
.link.small { font-size: 13px; }
.fold summary { cursor: pointer; padding: 12px 0; }
.sent { padding-left: 16px; }
.dots { display: inline-flex; gap: 4px; }
.dots i { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); opacity: 0.3; animation: pulse 1.2s infinite ease-in-out; }
.dots i:nth-child(2) { animation-delay: 0.2s; }
.dots i:nth-child(3) { animation-delay: 0.4s; }
@keyframes pulse { 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .dots i { animation: none; opacity: 0.7; } }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.warn { color: var(--warn, var(--ink-2)); }
.error { color: var(--fail); }
@media (max-width: 640px) {
  .page { padding: 14px 16px 40px; }
  .bar .btn-primary { width: 100%; justify-content: center; }
}
</style>
