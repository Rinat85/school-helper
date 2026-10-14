<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { api } from '../api'
import { dateRu, money } from '../format'
import { can, refreshHome, session } from '../session'
import { alertDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { Expense, ExpensesOverview } from '../types'

/*
 * Расходы класса. Родители видят утверждённые — это отчёт, с чеками.
 * Комитет видит ещё ждущие и отклонённые; ревизор утверждает здесь же
 * или кнопками в боте.
 */
const data = ref<ExpensesOverview | null>(null)
const error = ref('')
const busy = ref<number | null>(null)
const receipts = reactive<Record<number, string | 'loading' | 'error'>>({})

async function load(): Promise<void> {
  try {
    data.value = await api.expenses()
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(load)

onBeforeUnmount(() => {
  for (const url of Object.values(receipts)) {
    if (url.startsWith('blob:')) URL.revokeObjectURL(url)
  }
})

const pending = computed(() => data.value?.items.filter((e) => e.status === 'pending') ?? [])
const approved = computed(() => data.value?.items.filter((e) => e.status === 'approved') ?? [])
const rejected = computed(() => data.value?.items.filter((e) => e.status === 'rejected') ?? [])
const spent = computed(() => approved.value.reduce((sum, e) => sum + e.amount, 0))

const canCreate = computed(() => can('expense.create'))

/** Свой расход утвердить нельзя — кнопки показываем только чужим. */
function canDecide(e: Expense): boolean {
  return can('expense.approve') && e.created_by !== session.me?.person.id
}

async function showReceipt(e: Expense): Promise<void> {
  if (!e.receipt_id || receipts[e.id]) return
  receipts[e.id] = 'loading'
  try {
    receipts[e.id] = await api.fileUrl(e.receipt_id)
  } catch {
    receipts[e.id] = 'error'
  }
}

/* Причину отказа пишут прямо в карточке: window.prompt в Telegram работает не везде. */
const rejecting = ref<number | null>(null)
const reason = ref('')

function startReject(e: Expense): void {
  rejecting.value = e.id
  reason.value = ''
}

async function decide(e: Expense, approve: boolean): Promise<void> {
  if (busy.value !== null) return
  busy.value = e.id
  try {
    if (approve) await api.approveExpense(e.id)
    else await api.rejectExpense(e.id, reason.value.trim() || null)
    rejecting.value = null
    haptic(approve ? 'success' : 'warning')
    toast(approve ? `Записано в кассу: −${money(e.amount, true)}` : 'Отклонено, автору написал')
    await Promise.all([load(), refreshHome()])
  } catch (err) {
    haptic('error')
    await alertDialog((err as Error).message)
    await load()
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Расходы</h1>
    <p class="page-subtitle">Каждый расход утверждает ревизор — после этого он попадает в кассу</p>

    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!data" class="spinner" />

    <template v-else>
      <div class="section">
        <div class="card card-pad">
          <div class="big-number" :class="{ 'danger-text': data.balance < 0 }">
            {{ money(data.balance, true) }}
          </div>
          <div class="muted" style="font-size: 14px; margin-top: 4px">
            в кассе · потрачено {{ money(spent, true) }}
          </div>
        </div>
      </div>

      <div v-if="canCreate" class="section">
        <RouterLink to="/expenses/new" class="btn">＋ Расход</RouterLink>
        <p v-if="!data.has_auditor" class="section-note danger-text">
          В классе нет ревизора — расходы некому утвердить. Назначьте его во вкладке «Люди».
        </p>
      </div>

      <div v-if="pending.length" class="section">
        <div class="section-title">Ждут ревизора</div>
        <div v-for="e in pending" :key="e.id" class="card expense">
          <div class="row">
            <div class="row-main">
              <div class="row-title">{{ e.title }}</div>
              <div class="row-sub">{{ dateRu(e.spent_at) }} · вносит {{ e.created_by_name }}</div>
            </div>
            <div class="row-end amount">{{ money(e.amount, true) }}</div>
          </div>
          <div v-if="e.note" class="extra note">{{ e.note }}</div>
          <div class="extra">
            <template v-if="e.receipt_id">
              <button v-if="!receipts[e.id]" class="link-btn" type="button" @click="showReceipt(e)">
                Показать чек
              </button>
              <div v-else-if="receipts[e.id] === 'loading'" class="muted">Загружаю чек…</div>
              <div v-else-if="receipts[e.id] === 'error'" class="danger-text">
                Не удалось загрузить чек
              </div>
              <img v-else :src="receipts[e.id]" alt="Чек" />
            </template>
            <span v-else class="muted">Чек не приложен</span>
          </div>
          <template v-if="canDecide(e)">
            <div v-if="rejecting === e.id" class="extra">
              <label class="field reason">
                <span class="field-label">Причина — автор её увидит (необязательно)</span>
                <textarea v-model="reason" rows="2" maxlength="200" placeholder="Нет чека" />
              </label>
              <div class="btn-row">
                <button class="btn btn-danger" :disabled="busy !== null" @click="decide(e, false)">
                  Отклонить
                </button>
                <button class="btn btn-secondary" :disabled="busy !== null" @click="rejecting = null">
                  Отмена
                </button>
              </div>
            </div>
            <div v-else class="btn-row" style="padding: 4px 16px 16px">
              <button class="btn" :disabled="busy !== null" @click="decide(e, true)">
                Утвердить
              </button>
              <button class="btn btn-danger" :disabled="busy !== null" @click="startReject(e)">
                Отклонить
              </button>
            </div>
          </template>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Утверждённые</div>
        <div v-if="!approved.length" class="card empty">Расходов пока не было</div>
        <div v-else class="card">
          <template v-for="e in approved" :key="e.id">
            <button class="row expense-row" type="button" @click="showReceipt(e)">
              <div class="row-main">
                <div class="row-title">{{ e.title }}</div>
                <div class="row-sub">
                  {{ dateRu(e.spent_at) }} · внёс {{ e.created_by_name }}, утвердил
                  {{ e.approved_by_name }}
                </div>
                <div v-if="e.note" class="row-sub note">{{ e.note }}</div>
                <div v-if="e.receipt_id && !receipts[e.id]" class="row-sub link">чек →</div>
              </div>
              <div class="row-end amount">−{{ money(e.amount) }}</div>
            </button>
            <div v-if="receipts[e.id]" class="extra">
              <div v-if="receipts[e.id] === 'loading'" class="muted">Загружаю чек…</div>
              <div v-else-if="receipts[e.id] === 'error'" class="danger-text">
                Не удалось загрузить чек
              </div>
              <img v-else :src="receipts[e.id]" alt="Чек" />
            </div>
          </template>
        </div>
      </div>

      <div v-if="rejected.length" class="section">
        <div class="section-title">Отклонённые</div>
        <div class="card">
          <div v-for="e in rejected" :key="e.id" class="row muted">
            <div class="row-main">
              <div class="row-title">{{ e.title }}</div>
              <div class="row-sub">
                {{ dateRu(e.spent_at) }} · отклонил {{ e.approved_by_name }}
              </div>
              <div v-if="e.note" class="row-sub note">{{ e.note }}</div>
            </div>
            <div class="row-end">{{ money(e.amount) }}</div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.expense {
  margin-bottom: 10px;
}

.amount {
  color: var(--text);
  font-weight: 600;
  white-space: nowrap;
}

.extra {
  padding: 0 16px 12px;
  font-size: 15px;
}

.extra img {
  display: block;
  width: 100%;
  max-height: 420px;
  object-fit: contain;
  border-radius: 8px;
}

.note {
  white-space: pre-line;
}

.reason {
  padding: 0 0 10px;
}

.expense-row {
  width: 100%;
  border: 0;
  background: none;
  font: inherit;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.link {
  color: var(--link);
}
</style>
