<script setup lang="ts">
// A bottom sheet with "Done": the iOS pattern people know, keeping the screen underneath. Focus goes to its
// title when it opens and back where it came from when it closes.
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ open: boolean; title: string }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const head = ref<HTMLElement | null>(null)
let before: Element | null = null

watch(() => props.open, async (on) => {
  if (on) {
    before = document.activeElement
    await nextTick()
    head.value?.focus()
  } else if (before instanceof HTMLElement) {
    before.focus()
  }
})

function key(e: KeyboardEvent) { if (e.key === 'Escape' && props.open) emit('close') }
document.addEventListener('keydown', key)
onBeforeUnmount(() => document.removeEventListener('keydown', key))
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet">
      <div v-if="open" class="backdrop" @click.self="emit('close')">
        <section class="sheet" role="dialog" aria-modal="true" :aria-label="title">
          <header>
            <h2 ref="head" tabindex="-1">{{ title }}</h2>
            <button type="button" class="done" @click="emit('close')">Done</button>
          </header>
          <div class="body"><slot /></div>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.35); display: flex; align-items: flex-end; justify-content: center; z-index: 20; }
.sheet {
  width: 100%; max-width: 560px; max-height: 92vh; overflow: auto; background: var(--ground); border-radius: 16px 16px 0 0;
  padding: 8px 16px calc(20px + env(safe-area-inset-bottom));
}
header { display: flex; align-items: center; justify-content: space-between; gap: 12px; position: sticky; top: 0; background: var(--ground); padding: 10px 0; }
h2 { margin: 0; font-size: 1.15rem; font-weight: 600; outline: none; }
.done { min-height: 44px; min-width: 44px; padding: 0 8px; border: 0; background: none; color: var(--accent); font: inherit; font-weight: 600; font-size: 1rem; cursor: pointer; }
.body { display: flex; flex-direction: column; gap: 14px; }
.sheet-enter-active, .sheet-leave-active { transition: opacity 220ms ease; }
.sheet-enter-active .sheet, .sheet-leave-active .sheet { transition: transform 280ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.sheet-enter-from, .sheet-leave-to { opacity: 0; }
.sheet-enter-from .sheet, .sheet-leave-to .sheet { transform: translateY(40px); }
@media (prefers-reduced-motion: reduce) {
  .sheet-enter-active .sheet, .sheet-leave-active .sheet { transition: none; }
}
</style>
