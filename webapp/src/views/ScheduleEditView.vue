<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, api } from '../api'
import { dayTitle, todayIso } from '../format'
import { alertDialog, confirmDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { ScheduleDay } from '../types'

/*
 * Один экран на три случая:
 *   ?draft=12      — проверить черновик, распознанный из сообщения учительницы;
 *   ?date=2026-…   — изменить опубликованный день;
 *   без параметров — заполнить день вручную.
 */
const route = useRoute()
const router = useRouter()

const draftId = route.query.draft ? Number(route.query.draft) : null
const loading = ref(true)
const busy = ref(false)
const source = ref<ScheduleDay | null>(null)
const published = ref(false)

const date = ref(nextSchoolDay())
const lessons = ref('')
const bring = ref('')
const note = ref('')

function nextSchoolDay(): string {
  const day = new Date(`${todayIso()}T12:00:00`)
  day.setDate(day.getDate() + 1)
  if (day.getDay() === 0) day.setDate(day.getDate() + 1) // воскресенье -> понедельник
  return day.toLocaleDateString('sv-SE')
}

function fill(day: ScheduleDay): void {
  date.value = day.date
  lessons.value = day.lessons.join('\n')
  bring.value = day.bring.join('\n')
  note.value = day.note ?? ''
}

onMounted(async () => {
  try {
    if (draftId) {
      source.value = await api.scheduleDraft(draftId)
      fill(source.value)
    } else if (typeof route.query.date === 'string') {
      date.value = route.query.date
      fill(await api.scheduleDay(route.query.date))
      published.value = true
    }
  } catch (e) {
    // 404 на дату — просто пустой день, остальное показываем
    if (!(e instanceof ApiError && e.status === 404)) await alertDialog((e as Error).message)
  } finally {
    loading.value = false
  }
})

function lines(text: string): string[] {
  return text
    .split(/\n/)
    .map((s) => s.replace(/^\s*\d{1,2}\s*[.)]\s*/, '').trim())
    .filter(Boolean)
}

/** «С собой» можно написать и столбиком, и через запятую. */
function items(text: string): string[] {
  return text
    .split(/[\n,;]/)
    .map((s) => s.trim())
    .filter(Boolean)
}

const valid = computed(
  () => !!date.value && (lines(lessons.value).length > 0 || items(bring.value).length > 0 || !!note.value.trim()),
)

const isDraft = computed(() => source.value?.status === 'draft')

async function publish(): Promise<void> {
  if (!valid.value || busy.value) return
  busy.value = true
  try {
    await api.publishDay({
      date: date.value,
      lessons: lines(lessons.value),
      bring: items(bring.value),
      note: note.value.trim() || null,
      draft_id: isDraft.value ? draftId : null,
    })
    haptic('success')
    toast('Опубликовано')
    router.replace('/schedule')
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function reject(): Promise<void> {
  if (!draftId || busy.value) return
  busy.value = true
  try {
    await api.rejectDraft(draftId)
    toast('Черновик убран')
    router.replace('/schedule')
  } catch (e) {
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function withdraw(): Promise<void> {
  if (busy.value) return
  const ok = await confirmDialog(`Снять расписание на ${dayTitle(date.value).toLowerCase()}?`)
  if (!ok) return
  busy.value = true
  try {
    await api.withdrawDay(date.value)
    toast('Снято')
    router.replace('/schedule')
  } catch (e) {
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">{{ isDraft ? 'Проверка расписания' : 'Расписание на день' }}</h1>
    <p class="page-subtitle">{{ date ? dayTitle(date) : 'Выберите день' }}</p>

    <div v-if="loading" class="spinner" />

    <form v-else class="section" @submit.prevent="publish">
      <div v-if="source?.source_text" class="section">
        <div class="section-title">Сообщение учительницы</div>
        <div class="card card-pad source">{{ source.source_text }}</div>
        <p v-if="!source.lessons.length" class="section-note">
          Бот не нашёл здесь уроков. Если это расписание — перепишите его в поля ниже.
        </p>
      </div>

      <div class="card">
        <label class="field">
          <span class="field-label">День</span>
          <input v-model="date" type="date" required />
        </label>
        <label class="field">
          <span class="field-label">Уроки — каждый с новой строки</span>
          <textarea v-model="lessons" rows="6" placeholder="Математика&#10;Русский язык&#10;Чтение" />
        </label>
        <label class="field">
          <span class="field-label">Что взять с собой</span>
          <textarea v-model="bring" rows="3" placeholder="краски, альбом, спортивная форма" />
        </label>
        <label class="field">
          <span class="field-label">Примечание (необязательно)</span>
          <textarea v-model="note" rows="2" maxlength="500" placeholder="Например: уроки до 12:30" />
        </label>
      </div>
      <p class="section-note">
        Если есть что взять с собой, родителям в 19:00 накануне придёт напоминание.
      </p>

      <div class="btn-col">
        <button class="btn" type="submit" :disabled="!valid || busy">
          {{ busy ? 'Публикую…' : 'Опубликовать' }}
        </button>
        <button v-if="isDraft" class="btn btn-secondary" type="button" :disabled="busy" @click="reject">
          Это не расписание
        </button>
        <button v-if="published" class="btn btn-danger" type="button" :disabled="busy" @click="withdraw">
          Снять с публикации
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.source {
  white-space: pre-line;
  font-size: 15px;
}

.btn-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 20px;
}
</style>
