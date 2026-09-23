<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import ProgressBar from '../components/ProgressBar.vue'
import { dateRu, money } from '../format'
import { can } from '../session'
import type { Collection } from '../types'

const items = ref<Collection[] | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    items.value = await api.collections()
  } catch (e) {
    error.value = (e as Error).message
  }
})

const open = computed(() => items.value?.filter((c) => c.status === 'open') ?? [])
const closed = computed(() => items.value?.filter((c) => c.status === 'closed') ?? [])
</script>

<template>
  <div class="page">
    <h1 class="page-title">Сборы</h1>
    <p class="page-subtitle">Все сборы класса и сколько собрано</p>

    <div v-if="can('collection.create')" class="section">
      <RouterLink to="/collections/new" class="btn">＋ Новый сбор</RouterLink>
    </div>

    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!items" class="spinner" />

    <template v-else>
      <div class="section">
        <div class="section-title">Идут</div>
        <div class="card">
          <div v-if="!open.length" class="empty">Сейчас ничего не собираем</div>
          <RouterLink
            v-for="c in open"
            :key="c.id"
            :to="`/collections/${c.id}`"
            class="row chevron"
          >
            <div class="row-main stack">
              <div class="row-title">{{ c.title }}</div>
              <ProgressBar :value="c.collected" :max="c.target" />
              <div class="row-sub">
                {{ money(c.collected) }} из {{ money(c.target, true) }}
                <template v-if="c.due_date"> · до {{ dateRu(c.due_date) }}</template>
              </div>
            </div>
          </RouterLink>
        </div>
      </div>

      <div v-if="closed.length" class="section">
        <div class="section-title">Закрытые</div>
        <div class="card">
          <RouterLink
            v-for="c in closed"
            :key="c.id"
            :to="`/collections/${c.id}`"
            class="row chevron"
          >
            <div class="row-main">
              <div class="row-title">{{ c.title }}</div>
              <div class="row-sub">
                собрано {{ money(c.collected, true) }}
                <template v-if="c.closed_at"> · закрыт {{ dateRu(c.closed_at) }}</template>
              </div>
            </div>
          </RouterLink>
        </div>
      </div>
    </template>
  </div>
</template>
