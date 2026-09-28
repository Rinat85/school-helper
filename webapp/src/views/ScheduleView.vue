<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import { dayTitle } from '../format'
import { can } from '../session'
import type { ScheduleOverview } from '../types'

const data = ref<ScheduleOverview | null>(null)
const error = ref('')

onMounted(async () => {
  try {
    data.value = await api.schedule()
  } catch (e) {
    error.value = (e as Error).message
  }
})

/* Вчерашний день нужен только утром, пока не опубликован сегодняшний:
   «что было задано» — дальше он просто мешает. */
const days = computed(() => {
  const all = data.value?.days ?? []
  const today = data.value?.today ?? ''
  const upcoming = all.filter((d) => d.date >= today)
  return upcoming.length ? upcoming : all
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

      <div v-if="!days.length" class="section">
        <div class="card">
          <div class="empty">
            Расписания пока нет. Оно появится, когда учительница напишет его в чате.
          </div>
        </div>
      </div>

      <div v-for="d in days" :key="d.id" class="section">
        <div class="section-title">{{ dayTitle(d.date, data.today) }}</div>
        <div class="card">
          <div v-for="(lesson, i) in d.lessons" :key="i" class="row">
            <span class="lesson-no">{{ i + 1 }}</span>
            <div class="row-main">{{ lesson }}</div>
          </div>
          <div v-if="d.bring.length" class="row">
            <span class="lesson-no">🎒</span>
            <div class="row-main">
              <div class="row-sub">С собой</div>
              {{ d.bring.join(', ') }}
            </div>
          </div>
          <div v-if="d.note" class="row">
            <span class="lesson-no">📝</span>
            <div class="row-main note">{{ d.note }}</div>
          </div>
          <RouterLink
            v-if="editor"
            :to="`/schedule/edit?date=${d.date}`"
            class="row chevron edit-link"
          >
            <div class="row-main">Изменить</div>
          </RouterLink>
        </div>
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
