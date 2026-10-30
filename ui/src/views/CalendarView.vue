<script setup lang="ts">
// From above (B37, octo/month.py): a month of conversations, one number per day (never colour alone), tinted by minutes
// talked; a day opens as one timeline of its conversations and calendar events; a conversation opens its page.
import { computed, onMounted, ref, watch } from 'vue'
import { lab } from '../lab/api'
import { mss, speakerColors, todoLabel } from '../lab/format'
import { dayTitle, fillersText, grid, minutesText, monthTitle, shift, spoken, WEEKDAY_SHORT, type Show } from '../lab/month'
import { href } from '../lab/router'
import type { DayView, MonthView } from '../lab/types'

const props = defineProps<{ month?: string; day?: string }>()
const m = ref<MonthView | null>(null)
const d = ref<DayView | null>(null)
const error = ref('')

async function load() {
  error.value = ''
  try {
    if (props.day) { d.value = null; d.value = await lab.day(props.day) }
    else { m.value = await lab.month(props.month) }
  } catch (e) { error.value = e instanceof Error ? e.message : String(e) }
}
onMounted(load)
watch(() => [props.month, props.day], load)

// Conversations or fillers (Jay, 30 Oct), remembered on this device only.
const show = ref<Show>((() => { try { return localStorage.getItem('octo.calendar.show') === 'fillers' ? 'fillers' : 'talk' } catch { return 'talk' } })())
watch(show, (v) => { try { localStorage.setItem('octo.calendar.show', v) } catch { /* private window */ } })
const weeks = computed(() => (m.value ? grid(m.value, show.value) : []))
const thisMonth = computed(() => !!m.value && m.value.today.slice(0, 7) === m.value.month)
const hm = (t: number) => new Date(t * 1000).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
const n = (k: number, one: string) => `${k} ${one}${k === 1 ? '' : 's'}`

// Arrow keys move a day (left, right) or a week (up, down) across the month's links, as GitHub's graph learned to.
const gridEl = ref<HTMLElement | null>(null)
function move(e: KeyboardEvent) {
  const by = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[e.key]
  const at = (e.target as HTMLElement).dataset.date
  if (!by || !at) return
  const next = gridEl.value?.querySelector<HTMLElement>(`[data-date="${shift(at, by)}"]`)
  if (next) { e.preventDefault(); next.focus() }
}
</script>

<template>
  <main class="page">
    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <!-- a day -->
    <template v-if="day">
      <nav class="crumbs small"><a :href="href.month(day.slice(0, 7))">← {{ monthTitle(day.slice(0, 7)) }}</a></nav>
      <div class="head">
        <h1>{{ dayTitle(day) }}</h1>
        <div class="steps">
          <a v-if="d?.prev" class="btn btn-small" :href="href.day(d.prev)" :aria-label="`Earlier: ${dayTitle(d.prev)}`">‹ Earlier</a>
          <a v-if="d?.next" class="btn btn-small" :href="href.day(d.next)" :aria-label="`Later: ${dayTitle(d.next)}`">Later ›</a>
        </div>
      </div>
      <p v-if="!d && !error" class="muted loading" role="status">Loading…</p>
      <template v-else-if="d">
        <p class="muted">{{ d.totals.conversations ? `${n(d.totals.conversations, 'conversation')} · ${minutesText(d.totals.minutes)}` : 'No conversations this day' }}<template
          v-if="d.totals.fillers"> · your fillers {{ fillersText(d.totals.fillers) }}</template><template
          v-if="!d.events_kept"> · the calendar keeps two days either side, so meetings aren't shown here</template></p>
        <ol v-if="d.items.length" class="card timeline">
          <li v-for="it in d.items" :key="`${it.kind}-${it.id}`" :class="it.kind">
            <template v-if="it.kind === 'event'">
              <span class="time small muted">{{ hm(it.start) }}–{{ hm(it.end) }}</span>
              <span class="what">
                <span class="ev">{{ it.title || '(No title)' }}</span>
                <span v-if="it.people.length" class="small muted"> · <template v-for="(p, i) in it.people" :key="p.name">{{ i ? ', ' : '' }}<a
                  v-if="p.person_id" :href="href.person(p.person_id)">{{ p.name }}</a><template v-else>{{ p.name }}</template></template></span>
                <span v-if="!it.conversation && it.end * 1000 < Date.now()" class="small muted"> · nothing recorded</span>
              </span>
            </template>
            <a v-else class="conv" :href="href.conversation(it.id)">
              <span class="time small">{{ it.started_at ? hm(it.started_at) : '' }}<span class="muted"> · {{ mss(it.duration_s) }}</span></span>
              <span class="what">
                <span class="chips"><span v-for="sp in it.speakers" :key="sp.letter" class="chip" :class="speakerColors(it.speakers)[sp.letter]">{{ sp.name }}</span></span>
                <span v-if="it.one_thing" class="one">{{ it.one_thing }}</span>
                <span v-else-if="it.preview" class="muted small">“{{ it.preview }}”</span>
                <span v-if="it.fillers" class="small muted">Your fillers: {{ fillersText(it.fillers) }}</span>
              </span>
              <span class="todo small" :class="todoLabel(it.todo).tone"><span class="dot" aria-hidden="true" />{{ todoLabel(it.todo).text }}</span>
            </a>
          </li>
        </ol>
      </template>
    </template>

    <!-- a month -->
    <template v-else>
      <nav class="crumbs small"><a :href="href.conversations">← Conversations</a></nav>
      <div class="head">
        <h1>{{ m ? monthTitle(m.month) : 'Calendar' }}</h1>
        <div v-if="m" class="steps">
          <a class="btn btn-small" :href="href.month(m.prev)" :aria-label="`Earlier: ${monthTitle(m.prev)}`">‹</a>
          <a v-if="!thisMonth" class="btn btn-small" :href="href.calendar">This month</a>
          <a class="btn btn-small" :href="href.month(m.next)" :aria-label="`Later: ${monthTitle(m.next)}`">›</a>
        </div>
      </div>
      <p v-if="!m && !error" class="muted loading" role="status">Loading…</p>
      <template v-else-if="m">
        <dl class="totals">
          <div><dt>Conversations</dt><dd>{{ m.totals.conversations }}</dd></div>
          <div><dt>Talked</dt><dd>{{ minutesText(m.totals.minutes) }}</dd></div>
          <div><dt>Reviews answered</dt><dd>{{ m.totals.answered }}<span class="muted"> of {{ m.totals.reviews }}</span></dd></div>
          <div><dt>People</dt><dd>{{ m.totals.people }}</dd></div>
        </dl>
        <div class="seg" role="group" aria-label="Show on each day">
          <button type="button" :aria-pressed="show === 'talk'" @click="show = 'talk'">Conversations</button>
          <button type="button" :aria-pressed="show === 'fillers'" @click="show = 'fillers'">Your fillers</button>
        </div>
        <section class="card cal" :aria-label="`${monthTitle(m.month)}, a day at a time`">
          <div class="wk" aria-hidden="true"><span v-for="w in WEEKDAY_SHORT" :key="w">{{ w }}</span></div>
          <div ref="gridEl" class="days" @keydown="move">
            <template v-for="(w, i) in weeks" :key="i">
              <template v-for="(c, j) in w" :key="`${i}-${j}`">
                <span v-if="!c" class="cell blank" aria-hidden="true" />
                <span v-else-if="c.state !== 'day'" class="cell off" :class="{ today: c.date === m.today }" :aria-label="spoken(c)">
                  <span class="num">{{ Number(c.date.slice(8)) }}</span></span>
                <a v-else class="cell" :class="[`l${c.level}`, { today: c.date === m.today }]" :href="href.day(c.date)"
                   :data-date="c.date" :aria-label="spoken(c, show)">
                  <span class="num">{{ Number(c.date.slice(8)) }}</span>
                  <template v-if="show === 'talk'">
                    <span v-if="c.count" class="count">{{ c.count }}</span>
                    <span v-if="c.count" class="mins">{{ minutesText(c.minutes) }}</span>
                  </template>
                  <template v-else-if="c.fillers">
                    <span class="count">{{ c.fillers.rate }}</span>
                    <span class="mins">{{ c.fillers.fillers }} in {{ c.fillers.words }}</span>
                  </template>
                  <span v-else-if="c.count" class="count none" aria-hidden="true">–</span>
                  <span v-if="c.to_answer" class="mark" aria-hidden="true" title="A review to answer" />
                </a>
              </template>
            </template>
          </div>
          <div class="legend small muted" aria-hidden="true">
            <span>{{ show === 'talk' ? 'Less' : 'Fewer' }}</span><i class="l1" /><i class="l2" /><i class="l3" /><i class="l4" /><span>{{ show === 'talk' ? 'More time talked' : 'More fillers per 100 of your words' }}</span>
            <span class="sep" /><span class="key"><span class="mark" /> A review to answer</span>
          </div>
        </section>
        <p v-if="show === 'talk'" class="muted small">The number is that day's conversations. A day opens to them, with your calendar beside them.</p>
        <p v-else class="muted small"><template v-if="m.totals.fillers">This month: {{ fillersText(m.totals.fillers) }}. </template>The number is your fillers per 100 of your words that day, from your list, in conversations on the
          fillers' line (English, 100 or more of your words; – where none were). <a :href="href.fillers">Your fillers</a></p>
      </template>
    </template>
  </main>
</template>

<style scoped>
.page { width: 100%; max-width: 900px; margin: 0 auto; padding: 20px 24px 56px; display: flex; flex-direction: column; gap: 14px; }
.crumbs a { text-decoration: none; display: inline-block; padding: 8px 0; margin: -8px 0; }
.head { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
p { margin: 0; }
.steps { display: flex; gap: 8px; }
.steps .btn { min-width: 44px; justify-content: center; }
.totals { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 0; }
.totals div { padding: 10px 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); }
.totals dt { font-size: 12px; color: var(--muted); }
.totals dd { margin: 2px 0 0; font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
.cal { padding: 14px; display: flex; flex-direction: column; gap: 8px; }
.wk, .days { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 6px; }
.wk span { font-size: 12px; color: var(--muted); text-align: center; }
.cell { position: relative; min-height: 76px; border-radius: 10px; padding: 6px 8px; display: flex; flex-direction: column;
  gap: 2px; text-decoration: none; color: var(--ink); background: var(--surface); border: 1px solid var(--line); }
.cell.blank { border: 0; background: none; }
.cell.off { color: var(--muted); opacity: 0.55; background: none; border-style: dashed; }
a.cell:hover { border-color: var(--accent); }
a.cell:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.cell.today { box-shadow: inset 0 0 0 2px var(--accent); }
.num { font-size: 12px; color: var(--muted); }
.count.none { color: var(--faint); }
.seg { display: flex; align-self: flex-start; padding: 3px; background: var(--chip); border-radius: 10px; }
.seg button { border: 0; background: transparent; color: var(--muted); padding: 8px 14px; min-height: 40px; border-radius: 8px; font-size: 14px; cursor: pointer; }
.seg button[aria-pressed='true'] { background: var(--surface); color: var(--ink); font-weight: 500; }
.count { font-size: 22px; font-weight: 600; line-height: 1.1; font-variant-numeric: tabular-nums; }
.mins { font-size: 11px; color: var(--ink-2); }
/* minutes talked, four steps of the accent over the page's own background */
.l1 { background: color-mix(in srgb, var(--accent) 12%, var(--surface)); }
.l2 { background: color-mix(in srgb, var(--accent) 24%, var(--surface)); }
.l3 { background: color-mix(in srgb, var(--accent) 38%, var(--surface)); }
.l4 { background: color-mix(in srgb, var(--accent) 52%, var(--surface)); }
.mark { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--warn); }
.cell .mark { position: absolute; top: 8px; right: 8px; }
.legend { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.legend i { width: 14px; height: 14px; border-radius: 4px; border: 1px solid var(--line); }
.legend .sep { width: 12px; }
.key { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }
.timeline { list-style: none; margin: 0; padding: 4px 0; }
.timeline li { border-top: 1px solid var(--line); }
.timeline li:first-child { border-top: 0; }
.timeline .event, .conv { display: flex; align-items: center; gap: 14px; padding: 12px 16px; }
.conv { text-decoration: none; color: inherit; min-height: 44px; }
.conv:hover { background: var(--quote); }
.time { flex: none; width: 112px; font-variant-numeric: tabular-nums; }
.what { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.ev { font-weight: 500; color: var(--ink-2); }
.event .time { color: var(--muted); }
.event { background: color-mix(in srgb, var(--quote) 60%, transparent); }
.one { font-weight: 500; }
.chips { display: flex; flex-wrap: wrap; gap: 4px; }
.chip { padding: 1px 10px; border-radius: 999px; font-size: 13px; font-weight: 500; background: var(--c-soft); color: var(--c); }
.todo { flex: none; display: flex; align-items: center; gap: 6px; font-weight: 500; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex-shrink: 0; }
.todo.muted { color: var(--muted); font-weight: 400; }
.todo.warn { color: var(--warn); }
.todo.accent { color: var(--accent); }
.todo.done { color: var(--pass); font-weight: 400; }
.todo.fail { color: var(--fail); }
.small { font-size: 13px; }
.muted { color: var(--muted); }
.error { color: var(--fail); }
@media (max-width: 640px) {
  .page { padding: 14px 16px 40px; }
  .totals { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .cal { padding: 8px; }
  .wk, .days { gap: 3px; }
  .cell { min-height: 48px; padding: 4px; align-items: center; }
  .mins { display: none; }
  .count { font-size: 17px; }
  .cell .mark { top: 4px; right: 4px; width: 6px; height: 6px; }
  .timeline .event, .conv { flex-wrap: wrap; gap: 4px 12px; }
  .todo { width: 100%; padding-left: 0; }
}
</style>
