<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api'
import { WEEKDAY_NAMES } from '../format'
import { alertDialog, haptic } from '../telegram'
import { toast } from '../toast'

/*
 * Постоянное расписание: уроки по дням недели и кружки.
 * Сохранение создаёт новую версию с сегодняшнего дня — прошлые дни не меняются.
 */
const router = useRouter()

const DAYS = [1, 2, 3, 4, 5, 6] // воскресенье в школе не бывает

interface ClubRow {
  weekday: number
  title: string
  starts_at: string
  ends_at: string
}

const loading = ref(true)
const busy = ref(false)
const lessons = ref<Record<number, string>>(Object.fromEntries(DAYS.map((d) => [d, ''])))
const clubs = ref<ClubRow[]>([])

onMounted(async () => {
  try {
    const timetable = (await api.schedule()).timetable
    for (const day of timetable?.days ?? []) {
      lessons.value[day.weekday] = day.lessons.join('\n')
      for (const club of day.clubs) clubs.value.push({ weekday: day.weekday, ...club })
    }
  } catch (e) {
    await alertDialog((e as Error).message)
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

function addClub(): void {
  clubs.value.push({ weekday: 1, title: '', starts_at: '', ends_at: '' })
}

const valid = computed(
  () =>
    (DAYS.some((d) => lines(lessons.value[d]).length > 0) || clubs.value.length > 0) &&
    clubs.value.every((c) => c.title.trim() && c.starts_at && c.ends_at && c.starts_at < c.ends_at),
)

async function save(): Promise<void> {
  if (!valid.value || busy.value) return
  busy.value = true
  try {
    await api.saveTimetable({
      lessons: Object.fromEntries(DAYS.map((d) => [d, lines(lessons.value[d])])),
      clubs: clubs.value.map((c) => ({ ...c, title: c.title.trim() })),
    })
    haptic('success')
    toast('Расписание сохранено')
    router.replace('/schedule')
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <h1 class="page-title">Постоянное расписание</h1>
    <p class="page-subtitle">Родители видят его на каждый день, пока учительница не напишет иначе</p>

    <div v-if="loading" class="spinner" />

    <form v-else @submit.prevent="save">
      <div class="section">
        <div class="section-title">Уроки — каждый с новой строки</div>
        <div class="card">
          <label v-for="d in DAYS" :key="d" class="field">
            <span class="field-label">{{ WEEKDAY_NAMES[d - 1] }}</span>
            <textarea
              v-model="lessons[d]"
              :rows="d === 6 ? 2 : 5"
              :placeholder="d === 6 ? 'Если учатся по субботам' : 'Чтение\nМатематика'"
            />
          </label>
        </div>
      </div>

      <div class="section">
        <div class="section-title">Кружки</div>
        <div v-for="(club, i) in clubs" :key="i" class="card club">
          <label class="field">
            <span class="field-label">Название</span>
            <input v-model="club.title" maxlength="80" placeholder="Английский язык" required />
          </label>
          <label class="field">
            <span class="field-label">День</span>
            <select v-model.number="club.weekday">
              <option v-for="d in DAYS" :key="d" :value="d">{{ WEEKDAY_NAMES[d - 1] }}</option>
            </select>
          </label>
          <div class="field times">
            <label>
              <span class="field-label">С</span>
              <input v-model="club.starts_at" type="time" required />
            </label>
            <label>
              <span class="field-label">До</span>
              <input v-model="club.ends_at" type="time" required />
            </label>
          </div>
          <div class="field">
            <button type="button" class="link-btn danger-text" @click="clubs.splice(i, 1)">
              Убрать кружок
            </button>
          </div>
        </div>
        <button type="button" class="btn btn-secondary" @click="addClub">＋ Кружок</button>
      </div>

      <div class="btn-col">
        <button class="btn" type="submit" :disabled="!valid || busy">
          {{ busy ? 'Сохраняю…' : 'Сохранить' }}
        </button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.club {
  margin-bottom: 10px;
}

.field select {
  width: 100%;
  border: 0;
  background: none;
  color: var(--text);
  font: inherit;
  padding: 2px 0;
  outline: none;
}

.times {
  display: flex;
  gap: 16px;
}

.times label {
  flex: 1;
}

.btn-col {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 20px;
}
</style>
