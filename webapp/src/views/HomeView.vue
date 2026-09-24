<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import ProgressBar from '../components/ProgressBar.vue'
import { dateRu, money, plural, ROLE_NAMES } from '../format'
import { refreshHome, session } from '../session'
import type { MoneySummary } from '../types'

const summary = ref<MoneySummary | null>(null)

onMounted(async () => {
  await refreshHome()
  if (session.me?.sees_money) summary.value = await api.summary().catch(() => null)
})

/* По значимости, а не по алфавиту ключей: «председатель, казначей», а не наоборот. */
const ROLE_ORDER = ['chair', 'treasurer', 'auditor', 'teacher']

const roles = computed(() =>
  ROLE_ORDER.filter((r) => session.me?.roles.includes(r as never))
    .map((r) => ROLE_NAMES[r])
    .join(', '),
)

const home = computed(() => session.home)

/* «Что от меня ждут» — главный смысл экрана (SPEC §7.1), а не меню разделов. */
const nothingToDo = computed(
  () =>
    !home.value?.my_open_contributions.length &&
    !home.value?.payments_to_confirm &&
    !home.value?.not_connected,
)
</script>

<template>
  <div class="page">
    <h1 class="page-title">{{ session.me?.class.name }}</h1>
    <p class="page-subtitle">
      {{ session.me?.class.school }}
      <template v-if="roles"> · вы {{ roles }}</template>
    </p>

    <div class="section">
      <div class="section-title">Что от вас ждут</div>
      <div class="card">
        <div v-if="nothingToDo" class="card-pad muted">
          Всё в порядке — от вас сейчас ничего не нужно.
        </div>

        <RouterLink
          v-for="item in home?.my_open_contributions"
          :key="item.collection_id"
          :to="`/collections/${item.collection_id}`"
          class="row chevron"
        >
          <span style="font-size: 22px">💰</span>
          <div class="row-main">
            <div class="row-title">{{ item.title }}</div>
            <div class="row-sub">
              <template v-if="item.status === 'claimed'">ждёт подтверждения казначея</template>
              <template v-else-if="item.status === 'partial'">
                осталось {{ money(item.expected - item.paid, true) }}
                <template v-if="item.due_date"> · до {{ dateRu(item.due_date) }}</template>
              </template>
              <template v-else>
                взнос {{ money(item.expected, true) }}
                <template v-if="item.due_date"> · до {{ dateRu(item.due_date) }}</template>
              </template>
            </div>
          </div>
        </RouterLink>

        <RouterLink v-if="home?.payments_to_confirm" to="/payments" class="row chevron">
          <span style="font-size: 22px">🧾</span>
          <div class="row-main">
            <div class="row-title">Подтвердить платежи</div>
            <div class="row-sub">сверьте с выпиской по карте</div>
          </div>
          <span class="badge">{{ home.payments_to_confirm }}</span>
        </RouterLink>

        <RouterLink v-if="home?.not_connected" to="/people" class="row chevron">
          <span style="font-size: 22px">📵</span>
          <div class="row-main">
            <div class="row-title">Не подключили бота: {{ home.not_connected }}</div>
            <div class="row-sub">
              {{ plural(home.not_connected, 'ему', 'им', 'им') }} не приходят напоминания
            </div>
          </div>
        </RouterLink>
      </div>
    </div>

    <template v-if="summary">
      <div class="section">
        <div class="section-title">Касса класса</div>
        <div class="card card-pad">
          <div class="big-number">{{ money(summary.balance, true) }}</div>
          <div class="muted" style="font-size: 14px; margin-top: 4px">
            поступило {{ money(summary.inflow) }} · потрачено {{ money(summary.outflow) }}
          </div>
        </div>
      </div>

      <div v-if="summary.collections.length" class="section">
        <div class="section-title">Идут сборы</div>
        <div class="card">
          <RouterLink
            v-for="c in summary.collections"
            :key="c.id"
            :to="`/collections/${c.id}`"
            class="row chevron"
          >
            <div class="row-main stack">
              <div class="row-title">{{ c.title }}</div>
              <ProgressBar :value="c.collected" :max="c.target" />
              <div class="row-sub">
                {{ money(c.collected) }} из {{ money(c.target, true) }}
              </div>
            </div>
          </RouterLink>
        </div>
      </div>
    </template>
  </div>
</template>
