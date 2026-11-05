<script setup lang="ts">
// The conversation page's title, its state, and the ⋯ menu: the tools used rarely, opened at the bottom of the page.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { mss, sourceName, todoLabel, when } from '../../lab/format'
import { canDelete, canEdit } from '../../lab/where'
import type { SessionDetail } from '../../lab/types'

const props = defineProps<{ s: SessionDetail; retrying: boolean }>()
const emit = defineEmits<{ retry: []; tool: [which: 'split' | 'remove']; aside: [] }>()

const withWhom = computed(() => props.s.speakers.filter((sp) => !sp.self && sp.name && (sp.state === 'confirmed' || sp.state === 'auto'))
  .map((sp) => sp.name).join(', '))

const moreOpen = ref(false)
const moreEl = ref<HTMLElement | null>(null)
function closeOutside(e: Event) { if (moreOpen.value && !moreEl.value?.contains(e.target as Node)) moreOpen.value = false }
onMounted(() => document.addEventListener('click', closeOutside))
onUnmounted(() => document.removeEventListener('click', closeOutside))
defineExpose({ close: () => { moreOpen.value = false } })
function tool(which: 'split' | 'remove') {
  moreOpen.value = false
  emit('tool', which)
}
</script>

<template>
  <header class="head">
    <div class="grow">
      <h1>{{ when(s.started_at) }}</h1>
      <div class="muted meta">{{ mss(s.duration_s) }} · {{ sourceName(s.source) }}<template v-if="withWhom"> · with {{ withWhom }}</template><template
        v-if="s.language && s.language !== 'en'"> · language: {{ s.language }}</template><template v-if="!s.in_gate"> · not counted</template></div>
    </div>
    <div class="pill" :class="todoLabel(s.todo).tone"><span class="dot" aria-hidden="true" />{{ todoLabel(s.todo).text }}</div>
    <button v-if="s.todo.kind === 'failed'" type="button" class="btn btn-small" :disabled="retrying" @click="emit('retry')">Try again</button>
    <div ref="moreEl" class="more-wrap">
      <button type="button" class="more-btn" aria-haspopup="menu" :aria-expanded="moreOpen" aria-label="More: split, delete, leave someone out"
              @click="moreOpen = !moreOpen">
        <svg width="18" height="18" viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="8" r="1.5" fill="currentColor" /><circle cx="8" cy="8" r="1.5" fill="currentColor" /><circle cx="13" cy="8" r="1.5" fill="currentColor" /></svg>
      </button>
      <div v-if="moreOpen" class="menu-pop" role="menu">
        <template v-if="canEdit">
          <button type="button" role="menuitem" @click="tool('split')">Split into conversations…</button>
          <button type="button" role="menuitem" @click="tool('remove')">Delete or leave someone out…</button>
        </template>
        <button v-else-if="canDelete" type="button" role="menuitem" @click="tool('remove')">Delete this conversation…</button>
        <button v-if="!s.aside" type="button" role="menuitem" @click="moreOpen = false; emit('aside')">Not a conversation…</button>
        <p v-else class="muted small away-note">Deleting is at home or on your Mac. Splitting, and leaving someone
          out, are on your Mac.</p>
      </div>
    </div>
  </header>
</template>

<style scoped>
.head { display: flex; align-items: center; gap: 12px; }
h1 { margin: 0; font-size: 26px; font-weight: 600; }
.meta { margin-top: 2px; }
.grow { flex-grow: 1; min-width: 0; }
.muted { color: var(--muted); }
.small { font-size: 13px; }
.pill { display: flex; align-items: center; gap: 8px; padding: 7px 14px; border-radius: 999px; font-size: 14px; font-weight: 500; background: var(--chip); white-space: nowrap; }
/* a long reason (a sign-in, a model to start) wraps rather than running off a phone's screen */
.pill.fail { white-space: normal; border-radius: 14px; align-items: flex-start; }
.pill.fail .dot { margin-top: 6px; flex-shrink: 0; }
.pill.warn { color: var(--warn); background: var(--warn-soft); }
.pill.accent { color: var(--accent); background: var(--accent-soft); }
.pill.done { color: var(--pass); }
.pill.fail { color: var(--fail); background: var(--fail-soft); }
.pill.muted { color: var(--muted); }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; }
.more-wrap { position: relative; }
.more-btn { width: 40px; height: 40px; border-radius: 10px; border: 1px solid var(--line); background: var(--surface); color: var(--ink-2);
  display: flex; align-items: center; justify-content: center; cursor: pointer; }
.menu-pop { position: absolute; right: 0; top: 46px; z-index: 6; width: 270px; padding: 6px; display: flex; flex-direction: column;
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 12px 32px rgba(0, 0, 0, 0.18); }
.menu-pop button { text-align: left; border: 0; background: transparent; padding: 10px 12px; min-height: 44px; border-radius: 8px; cursor: pointer; }
.menu-pop button:hover { background: var(--quote); }
.away-note { margin: 0; padding: 10px 12px; }

@media (max-width: 760px) {
  .head { flex-wrap: wrap; }
  .head .grow { flex-basis: 100%; }
}
</style>
