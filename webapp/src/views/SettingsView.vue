<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import { can, session } from '../session'
import { alertDialog, confirmDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { ChatRole, Settings } from '../types'

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

const CHAT_ROLES: ChatRole[] = ['parents', 'committee', 'teacher']
const CHAT_NAMES: Record<ChatRole, string> = {
  parents: 'Родительский чат',
  committee: 'Чат комитета',
  teacher: 'Чат с учителем',
}

function chatOf(role: ChatRole) {
  return settings.value?.chats.find((c) => c.role === role)
}

async function unbind(role: ChatRole): Promise<void> {
  const ok = await confirmDialog(
    `Отвязать «${chatOf(role)?.title ?? CHAT_NAMES[role]}»? Бот останется в группе, ` +
      'удалить его оттуда можно вручную.',
  )
  if (!ok) return
  await act(() => api.unbindChat(role))
}

async function forgetTeacher(): Promise<void> {
  const ok = await confirmDialog(
    'Перестать читать сообщения учительницы автоматически? Пересылать расписание боту можно и дальше.',
  )
  if (!ok) return
  await act(() => api.forgetTeacher())
}

async function act(call: () => Promise<Settings>): Promise<void> {
  busy.value = true
  try {
    fill(await call())
    haptic('success')
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
        <div class="section-title">Чаты класса в Telegram</div>
        <div class="card">
          <div v-for="role in CHAT_ROLES" :key="role" class="row">
            <div class="row-main">
              <div class="row-title">{{ CHAT_NAMES[role] }}</div>
              <div class="row-sub">{{ chatOf(role)?.title ?? 'не подключён' }}</div>
            </div>
            <button
              v-if="chatOf(role) && can('class.edit')"
              class="btn btn-small btn-secondary"
              :disabled="busy"
              @click="unbind(role)"
            >
              Отвязать
            </button>
            <span v-else-if="!chatOf(role)" class="chip chip-warning">нет</span>
          </div>
          <div v-if="chatOf('teacher')" class="row">
            <div class="row-main">
              <div class="row-title">Учительница</div>
              <div class="row-sub">
                {{
                  settings.teacher
                    ? `${settings.teacher.name} — её расписание бот читает сам`
                    : 'не отмечена — пересылайте расписание боту'
                }}
              </div>
            </div>
            <button
              v-if="settings.teacher && can('class.edit')"
              class="btn btn-small btn-secondary"
              :disabled="busy"
              @click="forgetTeacher"
            >
              Забыть
            </button>
          </div>
        </div>
        <p class="section-note">
          Чтобы подключить чат, добавьте в него бота администратором и напишите там
          <code>/setup</code> — бот спросит в личке, что это за чат. В чат с учителем бот
          ничего не пишет. Учительницу он узнает, если в том чате ответить на её сообщение
          командой <code>/учитель</code>.
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
