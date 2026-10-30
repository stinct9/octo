<script setup lang="ts">
// B16: "this mattered", while it happens. The Mac's clock places it, in whatever conversation was going on;
// the review reads the minute before it. Tap just after the moment; "Marked" offers Undo for its 2 s (Jay, 22 Oct).
import { ref } from 'vue'
import type { Memos } from '../memos'
import { haptic } from '../haptics'

const props = defineProps<{ memos: Memos }>()
const s = props.memos.s
const busy = ref(false)
const done = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

async function mark() {
  busy.value = true
  const ok = await props.memos.mark()
  busy.value = false
  haptic(ok ? 'success' : 'error')
  done.value = ok
  clearTimeout(timer)
  if (ok) timer = setTimeout(() => { done.value = false }, 2000)
}

const undone = ref(false)
async function undo() {
  clearTimeout(timer)
  busy.value = true
  const ok = await props.memos.unmark()
  busy.value = false
  done.value = false
  haptic(ok ? 'light' : 'error')
  undone.value = ok
  if (ok) timer = setTimeout(() => { undone.value = false }, 2000)
}
</script>

<template>
  <div class="mark">
    <button type="button" class="btn" :disabled="busy" @click="mark"><template v-if="done"><span aria-hidden="true">⚑ </span>Marked</template><template v-else>Mark this moment</template></button>
    <button v-if="done" type="button" class="btn quiet undo" :disabled="busy" @click="undo">Undo</button>
  </div>
  <p v-if="s.markError" class="error small" role="alert">{{ s.markError }}</p>
  <p v-else class="hint small" aria-live="polite">{{ done ? 'Your Mac has it. The review reads the minute before.'
    : undone ? 'Taken back: not marked.' : 'Tap just after something that mattered.' }}</p>
</template>

<style scoped>
.mark { display: flex; gap: 8px; }
.mark > .btn:first-child { flex: 1; }
.undo { flex: 0 0 auto; width: auto; padding: 0 20px; }
</style>
