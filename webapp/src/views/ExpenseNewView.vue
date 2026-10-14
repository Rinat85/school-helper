<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api'
import { money, parseAmount, todayIso } from '../format'
import { alertDialog, haptic } from '../telegram'
import { toast } from '../toast'

/*
 * Новый расход. В кассу он попадёт, когда утвердит ревизор, — до этого
 * это только заявка. В группу ничего не пишется.
 */
const router = useRouter()

const title = ref('')
const amountText = ref('')
const spentAt = ref(todayIso())
const note = ref('')
const receipt = ref<string | null>(null) // base64 JPEG без префикса
const preview = ref('')
const preparing = ref(false)
const saving = ref(false)

const today = todayIso()
const amount = computed(() => parseAmount(amountText.value))
const valid = computed(
  () => title.value.trim().length > 0 && amount.value > 0 && !!spentAt.value && !preparing.value,
)

function formatAmount(): void {
  amountText.value = amount.value ? money(amount.value) : ''
}

onBeforeUnmount(() => {
  if (preview.value) URL.revokeObjectURL(preview.value)
})

/** Фото с телефона весит мегабайты — ужимаем до 1600px JPEG прямо здесь. */
async function pickReceipt(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  preparing.value = true
  try {
    const bitmap = await createImageBitmap(file)
    const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(bitmap.width * scale)
    canvas.height = Math.round(bitmap.height * scale)
    canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    bitmap.close()
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, 'image/jpeg', 0.85),
    )
    if (!blob) throw new Error('не удалось обработать фото')
    const dataUrl = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result))
      reader.onerror = () => reject(reader.error)
      reader.readAsDataURL(blob)
    })
    receipt.value = dataUrl.slice(dataUrl.indexOf(',') + 1)
    if (preview.value) URL.revokeObjectURL(preview.value)
    preview.value = URL.createObjectURL(blob)
  } catch {
    await alertDialog('Не получилось открыть фото. Попробуйте другое — JPEG или PNG.')
  } finally {
    preparing.value = false
  }
}

function dropReceipt(): void {
  receipt.value = null
  if (preview.value) URL.revokeObjectURL(preview.value)
  preview.value = ''
}

async function submit(): Promise<void> {
  if (!valid.value || saving.value) return
  saving.value = true
  try {
    const created = await api.createExpense({
      title: title.value.trim(),
      amount: amount.value,
      spent_at: spentAt.value,
      note: note.value.trim() || null,
      receipt: receipt.value,
    })
    haptic('success')
    const warnings: string[] = []
    if (created.shortfall) {
      warnings.push(`Если ревизор утвердит, касса уйдёт в минус на ${money(created.shortfall, true)}.`)
    }
    if (!created.auditors_notified) {
      warnings.push(
        'Ревизору сообщение не ушло: его нет в классе или он не подключил бота. ' +
          'Расход ждёт в разделе «Расходы».',
      )
    }
    if (warnings.length) await alertDialog(warnings.join('\n\n'))
    else toast('Отправлено ревизору на утверждение')
    router.replace('/expenses')
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Новый расход</h1>
    <p class="page-subtitle">В кассу попадёт после утверждения ревизором</p>

    <form @submit.prevent="submit">
      <div class="card">
        <label class="field">
          <span class="field-label">На что потратили</span>
          <input v-model="title" maxlength="120" placeholder="Подарок учителю" required />
        </label>
        <label class="field">
          <span class="field-label">Сколько, сум</span>
          <input
            v-model="amountText"
            inputmode="numeric"
            placeholder="250 000"
            required
            @blur="formatAmount"
          />
        </label>
        <label class="field">
          <span class="field-label">Когда</span>
          <input v-model="spentAt" type="date" :max="today" required />
        </label>
        <label class="field">
          <span class="field-label">Примечание (необязательно)</span>
          <textarea v-model="note" rows="2" maxlength="500" placeholder="Цветы и торт" />
        </label>
      </div>

      <div class="section">
        <div class="section-title">Чек</div>
        <div class="card card-pad">
          <div v-if="preparing" class="muted">Обрабатываю фото…</div>
          <template v-else-if="preview">
            <img :src="preview" alt="Чек" class="receipt" />
            <button type="button" class="link-btn danger-text" @click="dropReceipt">
              Убрать фото
            </button>
          </template>
          <label v-else class="link-btn pick">
            📷 Приложить фото чека
            <input type="file" accept="image/*" hidden @change="pickReceipt" />
          </label>
        </div>
        <p class="section-note">
          Чек увидят ревизор, а после утверждения — все родители. Копия придёт вам в бота.
        </p>
      </div>

      <div class="actions">
        <button class="btn" type="submit" :disabled="!valid || saving">
          {{ saving ? 'Отправляю…' : 'Отправить ревизору' }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.receipt {
  display: block;
  width: 100%;
  max-height: 360px;
  object-fit: contain;
  border-radius: 8px;
  margin-bottom: 10px;
}

.pick {
  display: block;
  cursor: pointer;
}

.actions {
  margin-top: 20px;
}
</style>
