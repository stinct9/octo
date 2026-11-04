<script setup lang="ts">
// The full review as headlines that open, each heard from its row ("Hear it first", 2 Oct): the one thing, reading the
// others, between the lines, the principles (held / partly / missed), try next time, the steps, and the questions beneath
// the surface. It asks the one question too (the owner, E2 in docs/coach-plan.md; one card since 4 Nov: the owner read a
// headline in the question, then scrolled here for what it meant): "What here was new to you?" is a tick on each row.
// Ticks, or "Nothing new this time", count the conversation as reviewed; the items ticked are what Progress counts as
// "something new". Answers can be changed later.
import { computed, ref, watch } from 'vue'
import HeldMark from '../HeldMark.vue'
import ReviewItem from '../ReviewItem.vue'
import type { FeedbackChange } from '../../lab/api'
import { remember, remembered } from '../../lab/remembered'
import { entries, heldCounts, itemQuote, newToYouEntries } from '../../lab/review'
import type { Answer, Evidence, Insights, ItemFeedback, Quote } from '../../lab/types'

const props = defineProps<{
  ins: Insights | null
  isReview: boolean
  feedback: Record<string, ItemFeedback>
  canHear: boolean
  isMe: (letter: string | null) => boolean
  who: (letter: string | null) => string
  color: Record<string, string>
  focusItem: string | null
  playingClip: string | null
  /** The one question: asked once there's a review to answer for. */
  ask: boolean
  answer: Answer | null
  /** Reviewed before 6 Oct by rating every item: `answer` is what was marked new then. */
  legacy?: boolean
  saving: boolean
  error: string
}>()
const emit = defineEmits<{
  hear: [item: string, q: Quote | null]
  play: [start: number]
  practise: [item: string]
  change: [item: string, c: FeedbackChange]
  submit: [picked: string[], nothingNew: boolean]
}>()

const findings = computed(() => props.ins?.conduct ?? [])
const tryNext = computed(() => findings.value.filter((f) => f.try_next_time))
const held = computed(() => heldCounts(findings.value))
const all = computed(() => entries(props.ins))
const fullCount = computed(() => all.value.length)

// ---- the one question ----
const askable = computed(() => newToYouEntries(props.ins))
// Ticked to start with: the answer given, or what was marked new item by item before 6 Oct.
const initial = () => new Set(props.answer ? props.answer.new
  : askable.value.filter((e) => props.feedback[e.id]?.novelty === 'new').map((e) => e.id))
const picked = ref<Set<string>>(initial())
const editing = ref(!props.answer)
// A new or changed answer (by content: the one shown for a review rated before 6 Oct is worked out
// afresh on every refresh, and a refresh mustn't close the list while it's being changed).
watch(() => JSON.stringify(props.answer), () => { picked.value = initial(); if (props.answer) editing.value = false })
const asking = computed(() => props.ask && editing.value)
function tick(id: string, on: boolean) {
  const x = new Set(picked.value)
  if (on) x.add(id)
  else x.delete(id)
  picked.value = x
}
function done() { if (picked.value.size) emit('submit', askable.value.filter((e) => picked.value.has(e.id)).map((e) => e.id), false) }
function nothing() { emit('submit', [], true) }
function change() { picked.value = initial(); editing.value = true }
function cancel() { picked.value = initial(); editing.value = false }
/** A row's part in the question: its tick while answering, "New to you" once answered. */
const q = (id: string) => ({
  tickable: asking.value && askable.value.some((e) => e.id === id),
  ticked: picked.value.has(id),
  isNew: props.ask && !editing.value && !!props.answer?.new.includes(id),
})
const chosen = computed(() => askable.value.filter((e) => props.answer?.new.includes(e.id)))
const said = computed(() => {
  const a = props.answer
  if (!a) return ''
  const what = a.nothing_new ? (props.legacy ? 'nothing marked new' : 'nothing new this time') : `${a.new.length} new to you`
  return props.legacy ? `Reviewed before 6 Oct, item by item · ${what}` : `Reviewed · ${what}`
})
// Offer to practise one of them, unless one already is the focus.
const offer = computed(() => (chosen.value.some((e) => e.id === props.focusItem) ? [] : chosen.value.slice(0, 3)))
const short = (t: string) => (t.length > 48 ? `${t.slice(0, 46).trimEnd()}…` : t)

const readAll = ref(remembered('octo:review-open', false))
const opened = ref<Set<string>>(new Set())
const isOpen = (id: string) => readAll.value !== opened.value.has(id)  // "open all" flips what's been toggled
const allOpen = computed(() => fullCount.value > 0 && all.value.every((e) => isOpen(e.id)))
function toggleItem(id: string) {
  const x = new Set(opened.value)
  if (x.has(id)) x.delete(id)
  else x.add(id)
  opened.value = x
}
function toggleAll() {
  readAll.value = !allOpen.value
  opened.value = new Set()
  remember('octo:review-open', readAll.value)
}

// A principle is heard in the owner's own words where it has them.
const quoteOf = (quotes: Quote[] | undefined, preferMe = false) => (props.canHear ? itemQuote(quotes, preferMe, props.isMe) : null)

/** Only what's worth saying about an item's evidence: a quote that wasn't found, a line unsure. */
function evidenceNote(e: Evidence): string {
  const parts = []
  if (e.unverified_quotes) parts.push(`${e.unverified_quotes} quote${e.unverified_quotes === 1 ? '' : 's'} not found in the transcript, left out`)
  return [...parts, ...e.flags].join(' · ')
}
/** Answers given item by item before 6 Oct, as they were. */
function ratedBefore(fb: ItemFeedback | undefined): string {
  if (!fb?.verdict || fb.verdict === 'wrong' || fb.verdict === 'partly') return ''
  return [fb.verdict, fb.novelty === 'new' ? 'new to you' : fb.novelty === 'knew' ? 'knew it' : null].filter(Boolean).join(' · ')
}
const play = (start: number) => emit('play', start)
const stepBody = (why: string, detail?: string) => (detail ? `${detail} Why: ${why}` : why)
</script>

<template>
  <section v-if="fullCount" class="card full" :class="{ waiting: ask && !answer }" aria-labelledby="full-h">
    <div class="full-head">
      <h2 id="full-h">The full review</h2>
      <button type="button" class="link" @click="toggleAll">{{ allOpen ? 'Close all' : 'Open all' }}</button>
    </div>
    <div v-if="asking" class="ask">
      <div class="ask-head">
        <h3 class="question">What here was new to you?</h3>
        <span class="muted small">Answering counts this conversation as reviewed</span>
      </div>
      <p class="muted small">Tick what you wouldn't have seen yourself. One is plenty.</p>
    </div>
    <div v-else-if="ask && answer" class="ask">
      <div class="ask-head">
        <h3 class="answered"><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d="m3 8.5 3.2 3L13 4.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
          {{ said }}</h3>
        <button type="button" class="link" aria-label="Change what was new to you" @click="change">Change</button>
      </div>
      <div v-if="offer.length" class="offer">
        <span class="muted small">Practise one of them?</span>
        <button v-for="e in offer" :key="e.id" type="button" class="practise" @click="emit('practise', e.id)">{{ short(e.title) }}</button>
      </div>
    </div>
    <div v-if="ins?.one_thing" class="group">
      <h3 class="sub">The one thing</h3>
      <ul class="items">
        <ReviewItem item="o" eyebrow="" :title="ins.one_thing.headline" :body="ins.one_thing.why" :quotes="ins.one_thing.quotes"
                    :open="isOpen('o')" :name-of="who" :color="color" no-answers :footnote="evidenceNote(ins.one_thing)"
                    :moment="quoteOf(ins.one_thing.quotes)" :playing="playingClip === 'i-o'" v-bind="q('o')"
                    @hear="emit('hear', 'o', quoteOf(ins.one_thing?.quotes))" @toggle="toggleItem('o')" @play="play" @tick="tick('o', $event)" />
      </ul>
    </div>
    <div v-if="ins?.reading?.length" class="group">
      <h3 class="sub">Reading the others</h3>
      <ul class="items">
        <ReviewItem v-for="(r, i) in ins.reading" :key="`r${i}`" :item="`r${i}`" :eyebrow="r.person" :title="r.headline"
                    :body="r.observation" :quotes="r.quotes" :feedback="feedback[`r${i}`]" :open="isOpen(`r${i}`)"
                    :name-of="who" :color="color" practise :focused="focusItem === `r${i}`" :footnote="evidenceNote(r)"
                    :rated="ratedBefore(feedback[`r${i}`])" :moment="quoteOf(r.quotes)" :playing="playingClip === `i-r${i}`"
                    @hear="emit('hear', `r${i}`, quoteOf(r.quotes))" @toggle="toggleItem(`r${i}`)" @play="play" v-bind="q(`r${i}`)" @tick="tick(`r${i}`, $event)"
                    @practise="emit('practise', `r${i}`)" @change="emit('change', `r${i}`, $event)" />
      </ul>
    </div>
    <div v-if="ins?.between?.length" class="group">
      <h3 class="sub">Between the lines</h3>
      <ul class="items">
        <ReviewItem v-for="(b, i) in ins.between" :key="`l${i}`" :item="`l${i}`" eyebrow="" :title="b.headline"
                    :body="b.observation" :quotes="b.quotes" :feedback="feedback[`l${i}`]" :open="isOpen(`l${i}`)"
                    :name-of="who" :color="color" practise :focused="focusItem === `l${i}`" :footnote="evidenceNote(b)"
                    :rated="ratedBefore(feedback[`l${i}`])" :moment="quoteOf(b.quotes)" :playing="playingClip === `i-l${i}`"
                    @hear="emit('hear', `l${i}`, quoteOf(b.quotes))" @toggle="toggleItem(`l${i}`)" @play="play" v-bind="q(`l${i}`)" @tick="tick(`l${i}`, $event)"
                    @practise="emit('practise', `l${i}`)" @change="emit('change', `l${i}`, $event)" />
      </ul>
    </div>
    <div v-if="findings.length" class="group">
      <h3 class="sub">Your principles
        <span v-if="held.length" class="held-sum"><span v-for="[h, n] in held" :key="h" class="hs"><HeldMark :held="h" bare /> {{ n }}</span></span></h3>
      <ul class="items">
        <template v-for="f in findings" :key="`p${f.principle}`">
          <ReviewItem :item="`p${f.principle}`" :eyebrow="`Principle ${f.principle}`" :title="f.title" :held="f.held"
                      :body="f.observation" :quotes="f.quotes" :feedback="feedback[`p${f.principle}`]" :open="isOpen(`p${f.principle}`)"
                      :name-of="who" :color="color" :practise="isReview" :focused="focusItem === `p${f.principle}`"
                      :footnote="evidenceNote(f)" :rated="ratedBefore(feedback[`p${f.principle}`])"
                      :moment="quoteOf(f.quotes, true)" :playing="playingClip === `i-p${f.principle}`"
                      @hear="emit('hear', `p${f.principle}`, quoteOf(f.quotes, true))"
                      v-bind="q(`p${f.principle}`)" @tick="tick(`p${f.principle}`, $event)"
                      @toggle="toggleItem(`p${f.principle}`)" @play="play" @practise="emit('practise', `p${f.principle}`)"
                      @change="emit('change', `p${f.principle}`, $event)" />
        </template>
      </ul>
    </div>
    <div v-if="tryNext.length" class="group">
      <h3 class="sub">Try next time</h3>
      <ul class="items">
        <ReviewItem v-for="f in tryNext" :key="`t${f.principle}`" :item="`t${f.principle}`" :eyebrow="`Within principle ${f.principle}`"
                    :title="f.try_next_time ?? ''" :feedback="feedback[`t${f.principle}`]" :open="isOpen(`t${f.principle}`)"
                    :name-of="who" :color="color" :rated="ratedBefore(feedback[`t${f.principle}`])"
                    v-bind="q(`t${f.principle}`)" @tick="tick(`t${f.principle}`, $event)"
                    @toggle="toggleItem(`t${f.principle}`)" @play="play" @change="emit('change', `t${f.principle}`, $event)" />
      </ul>
    </div>
    <div v-if="ins?.next?.length" class="group">
      <h3 class="sub">What to do</h3>
      <ul class="items">
        <ReviewItem v-for="(t, i) in ins.next" :key="`n${i}`" :item="`n${i}`" eyebrow="" :title="t.title ?? t.action ?? ''"
                    :body="stepBody(t.why, t.detail)" :open="isOpen(`n${i}`)" :name-of="who" :color="color" no-answers
                    v-bind="q(`n${i}`)" @tick="tick(`n${i}`, $event)" @toggle="toggleItem(`n${i}`)" @play="play" />
      </ul>
    </div>
    <div v-if="ins?.beneath.length" class="group">
      <h3 class="sub">Beneath the surface <span class="muted small">· questions, not findings</span></h3>
      <ul class="items">
        <ReviewItem v-for="(b, i) in ins.beneath" :key="`b${i}`" :item="`b${i}`" eyebrow="A question" :title="b.question"
                    :quotes="b.quotes" :feedback="feedback[`b${i}`]" :open="isOpen(`b${i}`)" :name-of="who" :color="color"
                    @toggle="toggleItem(`b${i}`)" @play="play" @change="emit('change', `b${i}`, $event)" />
      </ul>
    </div>
    <template v-if="asking">
      <p v-if="error" class="error small" role="alert">{{ error }}</p>
      <div class="answer">
        <button type="button" class="btn btn-primary" :disabled="!picked.size || saving" @click="done">
          {{ saving ? 'Saving…' : picked.size ? `Done · ${picked.size} new to me` : 'Done' }}</button>
        <button type="button" class="btn" :disabled="picked.size > 0 || saving"
                :title="picked.size ? 'Untick the items first' : undefined" @click="nothing">Nothing new this time</button>
        <button v-if="answer" type="button" class="link" @click="cancel">Cancel</button>
      </div>
    </template>
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.sub { margin: 0; font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

.full { padding: 16px 18px 10px; display: flex; flex-direction: column; gap: 14px; }
.full.waiting { border: 1.5px solid var(--accent); }
.ask { display: flex; flex-direction: column; gap: 6px; padding: 0 6px; }
.ask-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
.question { margin: 0; font-size: 16px; font-weight: 600; }
.answered { margin: 0; display: flex; align-items: center; gap: 8px; color: var(--pass); font-weight: 500; font-size: 15px; }
.ask p { margin: 0; }
.offer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.practise { font: inherit; font-size: 13px; font-weight: 500; border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
  padding: 5px 12px; min-height: 34px; border-radius: 10px; cursor: pointer; max-width: 100%; text-align: left; }
.answer { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding: 4px 6px 8px; }
.error { color: var(--fail); margin: 0; padding: 0 6px; }
.full-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.full-head .link { min-height: 36px; }
.group { display: flex; flex-direction: column; gap: 2px; }
.group .sub { padding: 0 6px; }
.items { list-style: none; margin: 0; padding: 0; }
.held-sum { display: inline-flex; align-items: center; gap: 14px; font-weight: 400; font-size: 13px; color: var(--ink-2); }
.hs { display: inline-flex; align-items: center; gap: 4px; }

@media (max-width: 760px) {
  .link { min-height: 44px; }
}
@media (max-width: 640px) {
  .answer .btn { flex: 1 1 100%; justify-content: center; }
  .practise { min-height: 44px; }
}
</style>
