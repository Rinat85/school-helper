<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api'
import { money, parseAmount, plural } from '../format'
import { refreshHome } from '../session'
import { alertDialog, confirmDialog, haptic } from '../telegram'
import { toast } from '../toast'

const router = useRouter()

const title = ref('')
const amountText = ref('')
const dueDate = ref('')
const purpose = ref('')
const saving = ref(false)

const amount = computed(() => parseAmount(amountText.value))
const valid = computed(() => title.value.trim().length > 0 && amount.value > 0)

// Сегодняшняя дата для min у календаря — прошлый срок сервер всё равно отклонит.
const today = new Date().toLocaleDateString('sv-SE') // YYYY-MM-DD в местном времени

function formatAmount(): void {
  amountText.value = amount.value ? money(amount.value) : ''
}

async function submit(): Promise<void> {
  if (!valid.value || saving.value) return
  const ok = await confirmDialog(
    `Объявить сбор «${title.value.trim()}» по ${money(amount.value, true)}?\n\n` +
      'Объявление уйдёт в группу класса и каждому родителю лично.',
  )
  if (!ok) return

  saving.value = true
  try {
    const created = await api.createCollection({
      title: title.value.trim(),
      amount_per_person: amount.value,
      due_date: dueDate.value || null,
      purpose: purpose.value.trim() || null,
    })
    haptic('success')
    toast(
      created.posted_to_group
        ? `Объявлено в группе и ${created.queued} ${plural(created.queued, 'родителю', 'родителям', 'родителям')} лично`
        : `Сбор создан. Группа не привязана — ${created.queued} ${plural(created.queued, 'родитель получит', 'родителя получат', 'родителей получат')} личное сообщение`,
    )
    await refreshHome()
    router.replace(`/collections/${created.id}`)
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
    <h1 class="page-title">Новый сбор</h1>
    <p class="page-subtitle">Все взносы добровольные — это будет сказано в объявлении</p>

    <form class="section" @submit.prevent="submit">
      <div class="card">
        <label class="field">
          <span class="field-label">На что собираем</span>
          <input v-model="title" maxlength="120" placeholder="Подарки на Новый год" required />
        </label>
        <label class="field">
          <span class="field-label">Сколько с человека, сум</span>
          <input
            v-model="amountText"
            inputmode="numeric"
            placeholder="50 000"
            @blur="formatAmount"
          />
        </label>
        <label class="field">
          <span class="field-label">До какого числа (необязательно)</span>
          <input v-model="dueDate" type="date" :min="today" />
        </label>
        <label class="field">
          <span class="field-label">Пояснение (необязательно)</span>
          <textarea
            v-model="purpose"
            maxlength="500"
            placeholder="Например: сладкие подарки и открытка для каждого ребёнка"
          />
        </label>
      </div>
      <p class="section-note">
        Код для сверки переводов и реквизиты карты подставятся в объявление сами.
      </p>

      <div style="margin-top: 20px">
        <button class="btn" type="submit" :disabled="!valid || saving">
          {{ saving ? 'Объявляю…' : 'Объявить сбор' }}
        </button>
      </div>
    </form>
  </div>
</template>
