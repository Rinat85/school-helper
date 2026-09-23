<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import ProgressBar from '../components/ProgressBar.vue'
import { dateRu, money, STATUS_NAMES } from '../format'
import { can, refreshHome } from '../session'
import { alertDialog, confirmDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { CollectionDetail, ContributionStatus, RosterRow } from '../types'

const props = defineProps<{ id: number }>()

const data = ref<CollectionDetail | null>(null)
const error = ref('')
const busy = ref(false)
/** Строка, у которой раскрыты действия. */
const expanded = ref<number | null>(null)

async function load(): Promise<void> {
  try {
    data.value = await api.collection(props.id)
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(load)

const isOpen = computed(() => data.value?.status === 'open')

/* Поимённый разрез приходит только казначею, председателю и ревизору.
   Группы — от того, что требует внимания, к тому, что уже в порядке. */
const GROUPS: { status: ContributionStatus[]; title: string; chip: string }[] = [
  { status: ['pending', 'partial'], title: 'Не сдали', chip: 'chip-danger' },
  { status: ['claimed'], title: 'Ждут подтверждения', chip: 'chip-warning' },
  { status: ['confirmed'], title: 'Сдали', chip: 'chip-success' },
  { status: ['waived'], title: 'Освобождены', chip: '' },
]

const groups = computed(() =>
  GROUPS.map((g) => ({
    ...g,
    rows: (data.value?.roster ?? []).filter((r) => g.status.includes(r.status)),
  })).filter((g) => g.rows.length),
)

const myStatus = computed(() => {
  const mine = data.value?.mine
  if (!mine) return null
  switch (mine.status) {
    case 'waived':
      return 'Вы освобождены от этого взноса'
    case 'confirmed':
      return `✅ Ваш взнос ${money(mine.paid, true)} принят`
    case 'claimed':
      return '⏳ Ваш взнос ждёт подтверждения казначея'
    case 'partial':
      return `Вы сдали ${money(mine.paid)} из ${money(mine.expected, true)}`
    default:
      return `Ваш взнос: ${money(mine.expected, true)}`
  }
})

async function run(action: () => Promise<unknown>, done: string): Promise<void> {
  if (busy.value) return
  busy.value = true
  try {
    await action()
    haptic('success')
    toast(done)
    expanded.value = null
    await load()
    await refreshHome()
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function markPaid(row: RosterRow, method: 'cash' | 'transfer'): Promise<void> {
  const how = method === 'cash' ? 'наличными' : 'переводом'
  const left = row.expected - row.paid // у частично сдавших — только остаток
  if (!(await confirmDialog(`Отметить, что ${row.name} сдал(а) ${money(left, true)} ${how}?`))) {
    return
  }
  await run(() => api.recordPayment(props.id, row.person_id, method, left), 'Записал в кассу')
}

async function toggleWaive(row: RosterRow): Promise<void> {
  const waive = row.status !== 'waived'
  const text = waive
    ? `Освободить ${row.name} от этого взноса? Это никому не будет видно, кроме комитета.`
    : `Вернуть ${row.name} в список сдающих?`
  if (!(await confirmDialog(text))) return
  await run(
    () => api.setWaived(props.id, row.person_id, waive),
    waive ? 'Освобождён от взноса' : 'Возвращён в список',
  )
}

async function closeCollection(): Promise<void> {
  if (!data.value) return
  const left = data.value.target - data.value.collected
  const text =
    left > 0
      ? `Закрыть сбор? Не собрано ещё ${money(left, true)}. Кнопка «Я оплатил» в группе исчезнет.`
      : 'Закрыть сбор? Кнопка «Я оплатил» в группе исчезнет.'
  if (!(await confirmDialog(text))) return
  await run(() => api.closeCollection(props.id), 'Сбор закрыт')
}
</script>

<template>
  <div class="page">
    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!data" class="spinner" />

    <template v-else>
      <h1 class="page-title">{{ data.title }}</h1>
      <p class="page-subtitle">
        <span class="chip" :class="isOpen ? 'chip-accent' : ''">
          {{ isOpen ? 'идёт' : 'закрыт' }}
        </span>
        по {{ money(data.amount_per_person, true) }}
        <template v-if="data.due_date"> · до {{ dateRu(data.due_date) }}</template>
      </p>

      <div class="section">
        <div class="card card-pad stack">
          <div class="big-number">{{ money(data.collected, true) }}</div>
          <ProgressBar :value="data.collected" :max="data.target" />
          <div class="muted" style="font-size: 14px">
            из {{ money(data.target, true) }} · {{ data.percent }}%
          </div>
        </div>
        <p v-if="data.purpose" class="section-note">{{ data.purpose }}</p>
      </div>

      <div v-if="data.payment_code || myStatus" class="section">
        <div class="card">
          <div v-if="myStatus" class="row">
            <div class="row-main">{{ myStatus }}</div>
          </div>
          <div v-if="data.payment_code" class="row">
            <div class="row-main">Код в комментарии к переводу</div>
            <div class="row-end"><code>{{ data.payment_code }}</code></div>
          </div>
        </div>
      </div>

      <!-- Поимённо — только комитету. Родителям этого блока в ответе API нет вовсе. -->
      <div v-for="g in groups" :key="g.title" class="section">
        <div class="section-title">{{ g.title }} · {{ g.rows.length }}</div>
        <div class="card">
          <template v-for="row in g.rows" :key="row.person_id">
            <button
              class="row"
              type="button"
              :disabled="!isOpen"
              @click="expanded = expanded === row.person_id ? null : row.person_id"
            >
              <div class="row-main">
                <div class="row-title">{{ row.name }}</div>
                <div class="row-sub">
                  <template v-if="row.child">{{ row.child }} · </template>
                  <template v-if="row.status === 'partial'">
                    сдал(а) {{ money(row.paid) }} из {{ money(row.expected) }}
                  </template>
                  <template v-else>{{ money(row.expected, true) }}</template>
                  <template v-if="!row.bot_connected"> · без бота</template>
                </div>
              </div>
              <span class="chip" :class="g.chip">{{ STATUS_NAMES[row.status] }}</span>
            </button>

            <div v-if="expanded === row.person_id && isOpen" class="actions">
              <template v-if="can('payment.confirm') && ['pending', 'partial'].includes(row.status)">
                <button class="btn btn-small" :disabled="busy" @click="markPaid(row, 'cash')">
                  Сдал наличными
                </button>
                <button
                  class="btn btn-small btn-secondary"
                  :disabled="busy"
                  @click="markPaid(row, 'transfer')"
                >
                  Перевод пришёл
                </button>
              </template>
              <RouterLink
                v-if="can('payment.confirm') && row.status === 'claimed'"
                to="/payments"
                class="btn btn-small"
              >
                К очереди платежей
              </RouterLink>
              <button
                v-if="can('collection.create') && row.status !== 'confirmed'"
                class="btn btn-small btn-secondary"
                :disabled="busy"
                @click="toggleWaive(row)"
              >
                {{ row.status === 'waived' ? 'Вернуть в список' : 'Освободить' }}
              </button>
            </div>
          </template>
        </div>
      </div>

      <div v-if="isOpen && can('collection.create')" class="section">
        <button class="btn btn-danger" :disabled="busy" @click="closeCollection">
          Закрыть сбор
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 4px 16px 14px;
}

button.row:disabled {
  cursor: default;
}
</style>
