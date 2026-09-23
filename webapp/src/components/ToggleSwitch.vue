<script setup lang="ts">
defineProps<{ modelValue: boolean; disabled?: boolean }>()
defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>

<template>
  <button
    type="button"
    role="switch"
    class="switch"
    :class="{ on: modelValue }"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="$emit('update:modelValue', !modelValue)"
  >
    <span class="knob" />
  </button>
</template>

<style scoped>
.switch {
  flex: none;
  width: 51px;
  height: 31px;
  border-radius: 16px;
  border: 0;
  padding: 2px;
  background: var(--soft);
  cursor: pointer;
  transition: background 0.2s;
}

.switch.on {
  background: var(--success);
}

.switch:disabled {
  opacity: 0.5;
  cursor: default;
}

.knob {
  display: block;
  width: 27px;
  height: 27px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2);
  transition: transform 0.2s;
}

.on .knob {
  transform: translateX(20px);
}
</style>
