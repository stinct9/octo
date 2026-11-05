<script setup lang="ts">
// Home: what you're working on, in one card (28 Oct review, item 4): your focus, and what the coach last said to work on.
// One fold for both; each keeps its own actions.
import { ref } from 'vue'
import CoachCard from './CoachCard.vue'
import FocusBanner from './FocusBanner.vue'
import FoldToggle from './FoldToggle.vue'

const open = ref(true)
</script>

<template>
  <section class="card working" aria-labelledby="wo-h">
    <div class="head">
      <h2 id="wo-h">Working on</h2>
      <FoldToggle v-model="open" name="working" label="what you're working on" />
    </div>
    <FocusBanner inside :shown="open" />
    <CoachCard inside :shown="open" />
  </section>
</template>

<style scoped>
.working { padding: 14px 18px; display: flex; flex-direction: column; gap: 10px; }
.head { display: flex; align-items: center; gap: 12px; }
h2 { margin: 0; flex: 1; font-size: 13px; color: var(--accent); font-weight: 500; }
</style>
