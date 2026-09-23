<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

import { api } from '../api'
import { dateRu, money } from '../format'
import { refreshHome } from '../session'
import { alertDialog, confirmDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { PendingPayment } from '../types'

const items = ref<PendingPayment[] | null>(null)
const error = ref('')
const busy = ref<number | null>(null)

/** Чеки грузим по требованию: каждый — запрос к Telegram через бэкенд. */
const receipts = reactive<Record<number, string | 'loading' | 'error'>>({})

async function load(): Promise<void> {
  try {
    items.value = await api.pending()
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

async function showReceipt(p: PendingPayment): Promise<void> {
  if (!p.receipt_id || receipts[p.id]) return
  receipts[p.id] = 'loading'
  try {
    receipts[p.id] = await api.fileUrl(p.receipt_id)
  } catch {
    receipts[p.id] = 'error'
  }
}

async function decide(p: PendingPayment, confirm: boolean): Promise<void> {
  if (busy.value !== null) return
  if (!confirm) {
    const ok = await confirmDialog(
      `Не видите перевод от ${p.name}? Родитель получит сообщение, что платёж не найден, ` +
        'и сможет прислать чек ещё раз.',
    )
    if (!ok) return
  }

  busy.value = p.id
  try {
    if (confirm) await api.confirmPayment(p.id)
    else await api.rejectPayment(p.id)
    haptic(confirm ? 'success' : 'warning')
    toast(confirm ? `Записал в кассу: ${money(p.amount, true)}` : 'Отклонено, родителю написал')
    items.value = items.value?.filter((x) => x.id !== p.id) ?? null
    await refreshHome()
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
    await load()
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Платежи</h1>
    <p class="page-subtitle">Родители отметили оплату — сверьте с выпиской и подтвердите</p>

    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!items" class="spinner" />
    <div v-else-if="!items.length" class="card empty">
      <div style="font-size: 36px">✅</div>
      Очередь пуста — всё подтверждено
    </div>

    <div v-for="p in items" :key="p.id" class="section">
      <div class="card">
        <div class="row">
          <div class="row-main">
            <div class="row-title">{{ p.name }}</div>
            <div class="row-sub">
              {{ p.collection_title }} · {{ p.method === 'cash' ? 'наличными' : 'переводом' }}
              · {{ dateRu(p.claimed_at) }}
            </div>
          </div>
          <div class="row-end" style="color: var(--text); font-weight: 600">
            {{ money(p.amount, true) }}
          </div>
        </div>

        <div v-if="p.receipt_id" class="receipt">
          <button
            v-if="!receipts[p.id]"
            class="link-btn"
            type="button"
            @click="showReceipt(p)"
          >
            Показать чек
          </button>
          <div v-else-if="receipts[p.id] === 'loading'" class="muted">Загружаю чек…</div>
          <div v-else-if="receipts[p.id] === 'error'" class="danger-text">
            Не удалось загрузить чек
          </div>
          <img v-else :src="receipts[p.id]" alt="Чек" />
        </div>
        <div v-else-if="p.method === 'transfer'" class="receipt muted">Чек не приложен</div>

        <div class="btn-row" style="padding: 4px 16px 16px">
          <button class="btn" :disabled="busy !== null" @click="decide(p, true)">
            Подтвердить
          </button>
          <button class="btn btn-danger" :disabled="busy !== null" @click="decide(p, false)">
            Не вижу
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.receipt {
  padding: 0 16px 12px;
  font-size: 15px;
}

.receipt img {
  display: block;
  width: 100%;
  max-height: 420px;
  object-fit: contain;
  border-radius: 8px;
  background: var(--soft);
}

.btn-danger {
  background: var(--soft);
}
</style>
