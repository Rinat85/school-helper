<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import { can, session } from '../session'
import { alertDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { Settings } from '../types'

const settings = ref<Settings | null>(null)
const error = ref('')
const busy = ref(false)

const name = ref('')
const school = ref('')
const cardNumber = ref('')
const cardHolder = ref('')

function fill(s: Settings): void {
  settings.value = s
  name.value = s.name
  school.value = s.school ?? ''
  cardNumber.value = groupDigits(s.card_number ?? '')
  cardHolder.value = s.card_holder ?? ''
}

onMounted(async () => {
  try {
    fill(await api.settings())
  } catch (e) {
    error.value = (e as Error).message
  }
})

/** 8600123412341234 -> «8600 1234 1234 1234», как на самой карте. */
function groupDigits(value: string): string {
  return value
    .replace(/\D/g, '')
    .slice(0, 19)
    .replace(/(\d{4})(?=\d)/g, '$1 ')
}

const infoDirty = computed(
  () =>
    settings.value &&
    (name.value.trim() !== settings.value.name ||
      (school.value.trim() || null) !== settings.value.school),
)

const cardDirty = computed(
  () =>
    settings.value &&
    (cardNumber.value.replace(/\D/g, '') !== (settings.value.card_number ?? '') ||
      (cardHolder.value.trim() || null) !== settings.value.card_holder),
)

async function save(patch: Partial<Settings>): Promise<void> {
  busy.value = true
  try {
    fill(await api.saveSettings(patch))
    if (session.me && patch.name) session.me.class.name = patch.name
    if (session.me && patch.school !== undefined) session.me.class.school = patch.school || null
    haptic('success')
    toast('Сохранено')
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}

function saveInfo(): Promise<void> {
  // Пустая строка, а не null: null сервер понимает как «не менять», а школу нужно уметь стереть.
  return save({ name: name.value.trim(), school: school.value.trim() })
}

function saveCard(): Promise<void> {
  return save({
    card_number: cardNumber.value.replace(/\D/g, ''),
    card_holder: cardHolder.value.trim(),
  })
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Класс</h1>
    <p class="page-subtitle">Настройки, которые видят все родители</p>

    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!settings" class="spinner" />

    <template v-else>
      <div class="section">
        <div class="card">
          <div class="row">
            <div class="row-main">Группа класса в Telegram</div>
            <span class="chip" :class="settings.group_bound ? 'chip-success' : 'chip-warning'">
              {{ settings.group_bound ? 'привязана' : 'не привязана' }}
            </span>
          </div>
        </div>
        <p v-if="!settings.group_bound" class="section-note">
          Добавьте бота в группу класса администратором и напишите там <code>/setup</code>.
          Пока группа не привязана, объявления о сборах уходят только в личку.
        </p>
      </div>

      <form v-if="can('class.edit')" class="section" @submit.prevent="saveInfo">
        <div class="section-title">Класс</div>
        <div class="card">
          <label class="field">
            <span class="field-label">Название</span>
            <input v-model="name" maxlength="40" placeholder="1 «В»" required />
          </label>
          <label class="field">
            <span class="field-label">Школа</span>
            <input v-model="school" maxlength="80" placeholder="Школа №101" />
          </label>
        </div>
        <button v-if="infoDirty" class="btn" style="margin-top: 12px" :disabled="busy">
          Сохранить
        </button>
      </form>

      <form v-if="can('collection.create')" class="section" @submit.prevent="saveCard">
        <div class="section-title">Реквизиты для переводов</div>
        <div class="card">
          <label class="field">
            <span class="field-label">Номер карты</span>
            <input
              :value="cardNumber"
              inputmode="numeric"
              autocomplete="off"
              placeholder="8600 0000 0000 0000"
              @input="cardNumber = groupDigits(($event.target as HTMLInputElement).value)"
            />
          </label>
          <label class="field">
            <span class="field-label">Имя получателя</span>
            <input v-model="cardHolder" maxlength="64" placeholder="Мария К." />
          </label>
        </div>
        <p class="section-note">
          Показываются в каждом объявлении о сборе. Деньги идут на личную карту —
          поэтому все поступления и расходы видны родителям в кассе.
        </p>
        <button v-if="cardDirty" class="btn" style="margin-top: 12px" :disabled="busy">
          Сохранить
        </button>
      </form>
    </template>
  </div>
</template>
