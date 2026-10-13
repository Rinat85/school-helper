<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import { WEEKDAY_NAMES, dayTitle, isoWeekday } from '../format'
import { can } from '../session'
import type { Club, ScheduleDay, ScheduleOverview } from '../types'

const data = ref<ScheduleOverview | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    data.value = await api.schedule()
  } catch (e) {
    error.value = (e as Error).message
  }
})

interface Entry {
  date: string
  /** опубликованное со слов учительницы — главнее постоянного */
  day: ScheduleDay | null
  /** уроки по постоянному расписанию, если на день ничего не опубликовано */
  lessons: string[]
  clubs: Club[]
}

function weekDay(date: string) {
  return data.value?.timetable?.days.find((d) => d.weekday === isoWeekday(date))
}

function addDays(date: string, n: number): string {
  const day = new Date(`${date}T12:00:00`)
  day.setDate(day.getDate() + n)
  return day.toLocaleDateString('sv-SE')
}

/* Опубликованные дни плюс сегодня и ближайший учебный день по постоянному
   расписанию. Вчерашний опубликованный нужен только утром, пока нет
   сегодняшнего: «что было задано» — дальше он просто мешает. */
const entries = computed<Entry[]>(() => {
  const all = data.value?.days ?? []
  const today = data.value?.today ?? ''
  const upcoming = all.filter((d) => d.date >= today)
  const published = upcoming.length ? upcoming : all

  const dates = new Set(published.map((d) => d.date))
  if (data.value?.timetable) {
    if (weekDay(today)) dates.add(today)
    for (let n = 1; n <= 7; n++) {
      const date = addDays(today, n)
      if (weekDay(date)?.lessons.length) {
        dates.add(date)
        break
      }
    }
  }

  return [...dates].sort().map((date) => {
    const day = published.find((d) => d.date === date) ?? null
    const base = weekDay(date)
    return { date, day, lessons: day ? day.lessons : (base?.lessons ?? []), clubs: base?.clubs ?? [] }
  })
})

const editor = computed(() => can('timetable.edit'))
</script>

<template>
  <div class="page">
    <h1 class="page-title">Расписание</h1>
    <p class="page-subtitle">Уроки и что взять с собой — со слов учительницы</p>

    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!data" class="spinner" />

    <template v-else>
      <div v-if="data.drafts?.length" class="section">
        <div class="section-title">Ждут проверки</div>
        <div class="card">
          <RouterLink
            v-for="d in data.drafts"
            :key="d.id"
            :to="`/schedule/edit?draft=${d.id}`"
            class="row chevron"
          >
            <div class="row-main">
              <div class="row-title">{{ dayTitle(d.date, data.today) }}</div>
              <div class="row-sub">
                {{ d.lessons.length ? d.lessons.join(', ') : 'не распознано — заполнить вручную' }}
              </div>
            </div>
          </RouterLink>
        </div>
        <p class="section-note">
          Бот распознал сообщения учительницы. Родители увидят день, когда вы его опубликуете.
        </p>
      </div>

      <div v-if="editor" class="section">
        <RouterLink to="/schedule/edit" class="btn btn-secondary">＋ Заполнить день</RouterLink>
      </div>

      <div v-if="!entries.length" class="section">
        <div class="card">
          <div class="empty">
            Расписания пока нет. Оно появится, когда учительница напишет его в чате.
          </div>
        </div>
      </div>

      <div v-for="e in entries" :key="e.date" class="section">
        <div class="section-title">{{ dayTitle(e.date, data.today) }}</div>
        <div class="card">
          <div v-for="(lesson, i) in e.lessons" :key="i" class="row">
            <span class="lesson-no">{{ i + 1 }}</span>
            <div class="row-main">{{ lesson }}</div>
          </div>
          <template v-if="e.day">
            <div v-if="e.day.bring.length" class="row">
              <span class="lesson-no">🎒</span>
              <div class="row-main">
                <div class="row-sub">С собой</div>
                {{ e.day.bring.join(', ') }}
              </div>
            </div>
            <div v-if="e.day.note" class="row">
              <span class="lesson-no">📝</span>
              <div class="row-main note">{{ e.day.note }}</div>
            </div>
          </template>
          <div v-for="(club, i) in e.clubs" :key="`c${i}`" class="row">
            <span class="lesson-no">⭐</span>
            <div class="row-main">
              <div class="row-sub">Кружок · {{ club.starts_at }}–{{ club.ends_at }}</div>
              {{ club.title }}
            </div>
          </div>
          <RouterLink
            v-if="editor"
            :to="e.day ? `/schedule/edit?date=${e.date}` : `/schedule/edit?base=${e.date}`"
            class="row chevron edit-link"
          >
            <div class="row-main">{{ e.day ? 'Изменить' : 'Уточнить на этот день' }}</div>
          </RouterLink>
        </div>
        <p v-if="!e.day && e.lessons.length" class="section-note">
          По постоянному расписанию — учительница может поменять.
        </p>
      </div>

      <div v-if="data.timetable" class="section">
        <div class="section-title">Обычная неделя</div>
        <div class="card">
          <div v-for="d in data.timetable.days" :key="d.weekday" class="row">
            <div class="row-main">
              <div class="row-title">{{ WEEKDAY_NAMES[d.weekday - 1] }}</div>
              <div v-if="d.lessons.length" class="row-sub">
                {{ d.lessons.map((l, i) => `${i + 1}. ${l}`).join('  ') }}
              </div>
              <div v-for="(club, i) in d.clubs" :key="i" class="row-sub">
                ⭐ {{ club.title }}, {{ club.starts_at }}–{{ club.ends_at }}
              </div>
            </div>
          </div>
          <RouterLink v-if="editor" to="/schedule/week" class="row chevron edit-link">
            <div class="row-main">Изменить неделю</div>
          </RouterLink>
        </div>
      </div>
      <div v-else-if="editor" class="section">
        <RouterLink to="/schedule/week" class="btn btn-secondary">＋ Постоянное расписание</RouterLink>
        <p class="section-note">
          Уроки и кружки по дням недели. Родители увидят их на каждый день, пока учительница
          не напишет иначе.
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.lesson-no {
  width: 22px;
  text-align: center;
  color: var(--hint);
  font-variant-numeric: tabular-nums;
}

.note {
  white-space: pre-line;
}

.edit-link {
  color: var(--accent);
}
</style>
