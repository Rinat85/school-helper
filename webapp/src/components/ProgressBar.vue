<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ value: number; max: number }>()

const percent = computed(() =>
  props.max > 0 ? Math.min(100, Math.round((100 * props.value) / props.max)) : 0,
)
</script>

<template>
  <div class="bar" role="progressbar" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100">
    <div class="fill" :class="{ done: percent >= 100 }" :style="{ width: `${percent}%` }" />
  </div>
</template>

<style scoped>
.bar {
  height: 8px;
  border-radius: 4px;
  background: var(--soft);
  overflow: hidden;
}

.fill {
  height: 100%;
  border-radius: 4px;
  background: var(--accent);
  transition: width 0.3s ease;
}

.fill.done {
  background: var(--success);
}
</style>
