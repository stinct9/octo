<script setup lang="ts">
// The full review as headlines that open, each heard from its row ("Hear it first", 2 Oct): reading the others,
// between the lines, the principles (held / partly / missed), try next time, and the questions beneath the surface.
import { computed, ref } from 'vue'
import HeldMark from '../HeldMark.vue'
import ReviewItem from '../ReviewItem.vue'
import type { FeedbackChange } from '../../lab/api'
import { remember, remembered } from '../../lab/remembered'
import { entries, heldCounts, itemQuote } from '../../lab/review'
import type { Evidence, Insights, ItemFeedback, Quote } from '../../lab/types'

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
}>()
const emit = defineEmits<{
  hear: [item: string, q: Quote | null]
  play: [start: number]
  practise: [item: string]
  change: [item: string, c: FeedbackChange]
}>()

const findings = computed(() => props.ins?.conduct ?? [])
const tryNext = computed(() => findings.value.filter((f) => f.try_next_time))
const held = computed(() => heldCounts(findings.value))
const all = computed(() => entries(props.ins))
const fullCount = computed(() => all.value.filter((e) => !['one', 'step'].includes(e.group)).length)

const readAll = ref(remembered('octo:review-open', false))
const opened = ref<Set<string>>(new Set())
const isOpen = (id: string) => readAll.value !== opened.value.has(id)  // "open all" flips what's been toggled
const allOpen = computed(() => fullCount.value > 0 && all.value.filter((e) => !['one', 'step'].includes(e.group)).every((e) => isOpen(e.id)))
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
</script>

<template>
  <section v-if="fullCount" class="card full" aria-labelledby="full-h">
    <div class="full-head">
      <h2 id="full-h">The full review</h2>
      <button type="button" class="link" @click="toggleAll">{{ allOpen ? 'Close all' : 'Open all' }}</button>
    </div>
    <div v-if="ins?.reading?.length" class="group">
      <h3 class="sub">Reading the others</h3>
      <ul class="items">
        <ReviewItem v-for="(r, i) in ins.reading" :key="`r${i}`" :item="`r${i}`" :eyebrow="r.person" :title="r.headline"
                    :body="r.observation" :quotes="r.quotes" :feedback="feedback[`r${i}`]" :open="isOpen(`r${i}`)"
                    :name-of="who" :color="color" practise :focused="focusItem === `r${i}`" :footnote="evidenceNote(r)"
                    :rated="ratedBefore(feedback[`r${i}`])" :moment="quoteOf(r.quotes)" :playing="playingClip === `i-r${i}`"
                    @hear="emit('hear', `r${i}`, quoteOf(r.quotes))" @toggle="toggleItem(`r${i}`)" @play="play"
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
                    @hear="emit('hear', `l${i}`, quoteOf(b.quotes))" @toggle="toggleItem(`l${i}`)" @play="play"
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
                    @toggle="toggleItem(`t${f.principle}`)" @play="play" @change="emit('change', `t${f.principle}`, $event)" />
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
  </section>
</template>

<style scoped>
h2 { margin: 0; font-size: 17px; font-weight: 600; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.link { border: 0; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; }
.sub { margin: 0; font-size: 15px; font-weight: 600; display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }

.full { padding: 16px 18px 10px; display: flex; flex-direction: column; gap: 14px; }
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
</style>
