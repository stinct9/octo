<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { LogLine } from '../types'

const props = defineProps<{ logs: LogLine[] }>()
const showTool = ref(false)
const box = ref<HTMLElement | null>(null)
const pinned = ref(true) // auto-scroll until the reader scrolls up

const lines = computed(() => props.logs.filter((l) => showTool.value || l.stream === 'device'))

function onScroll(): void {
  const el = box.value
  if (el) pinned.value = el.scrollHeight - el.scrollTop - el.clientHeight < 24
}

watch(() => lines.value.length, async () => {
  if (!pinned.value) return
  await nextTick()
  if (box.value) box.value.scrollTop = box.value.scrollHeight
})

function tone(line: string): string {
  if (/\[E\]|\[W\]|fail|error|mismatch/i.test(line)) return 'err'
  return ''
}
</script>

<template>
  <section class="log" aria-label="Device log">
    <div class="head">
      <span class="title">Device log</span>
      <span class="spacer" />
      <label class="toggle"><input v-model="showTool" type="checkbox" /> Show compiler &amp; uploader output</label>
    </div>
    <div ref="box" class="body mono" tabindex="0" @scroll="onScroll">
      <div v-if="!lines.length" class="empty">Nothing yet.</div>
      <div v-for="(l, i) in lines" :key="i" :class="[tone(l.line), l.stream]">{{ l.line }}</div>
    </div>
  </section>
</template>

<style scoped>
.log { background: var(--log-bg); border-radius: 12px; padding: 14px 16px; display: flex; flex-direction: column; gap: 8px; min-height: 160px; flex-grow: 1; }
.head { display: flex; align-items: center; gap: 10px; }
.title { font-size: 12px; font-weight: 600; letter-spacing: 0.6px; text-transform: uppercase; color: var(--log-muted); }
.spacer { flex-grow: 1; }
.toggle { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--log-muted); cursor: pointer; }
.body { flex-grow: 1; overflow-y: auto; font-size: 13px; line-height: 1.75; color: var(--log-ink); white-space: pre-wrap; overflow-wrap: anywhere; min-height: 0; }
.body .tool { color: var(--log-muted); }
.body .err { color: var(--log-err); }
.empty { color: var(--log-muted); }
</style>
