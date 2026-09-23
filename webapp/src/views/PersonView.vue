<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '../api'
import ToggleSwitch from '../components/ToggleSwitch.vue'
import { can, loadSession, refreshHome, session } from '../session'
import { alertDialog, confirmDialog, haptic } from '../telegram'
import { toast } from '../toast'
import type { AssignableRole, Person } from '../types'

const props = defineProps<{ id: number }>()
const router = useRouter()

const person = ref<Person | null>(null)
const error = ref('')
const busy = ref(false)
const name = ref('')
const child = ref('')

const ROLES: { role: AssignableRole; title: string; hint: string }[] = [
  {
    role: 'chair',
    title: 'Председатель',
    hint: 'Управляет людьми и ролями, закрывает сборы, утверждает расходы',
  },
  {
    role: 'treasurer',
    title: 'Казначей',
    hint: 'Объявляет сборы, подтверждает платежи, видит, кто сдал',
  },
  {
    role: 'auditor',
    title: 'Ревизор',
    hint: 'Видит всю кассу поимённо и утверждает расходы, но сам денег не проводит',
  },
  {
    role: 'teacher',
    title: 'Учитель',
    hint: 'Объявления и расписание. Денежный раздел не видит вообще',
  },
]

async function load(): Promise<void> {
  try {
    const list = await api.people()
    person.value = list.find((p) => p.id === props.id) ?? null
    if (!person.value) error.value = 'Человек не найден'
    name.value = person.value?.name ?? ''
    child.value = person.value?.child ?? ''
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(load)

const isMe = computed(() => person.value?.id === session.me?.person.id)
const dirty = computed(
  () =>
    person.value &&
    (name.value.trim() !== person.value.name || (child.value.trim() || null) !== person.value.child),
)

async function toggleRole(role: AssignableRole, enabled: boolean): Promise<void> {
  if (!person.value || busy.value) return
  busy.value = true
  try {
    person.value = await api.setRole(person.value.id, role, enabled)
    haptic('success')
    // Свои права поменялись — перечитываем, чтобы вкладки внизу совпадали с ролью.
    if (isMe.value) await loadSession()
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function saveNames(): Promise<void> {
  if (!person.value || !name.value.trim() || busy.value) return
  busy.value = true
  try {
    person.value = await api.editPerson(person.value.id, name.value.trim(), child.value.trim() || null)
    haptic('success')
    toast('Сохранено')
  } catch (e) {
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function removeFromClass(): Promise<void> {
  if (!person.value) return
  const ok = await confirmDialog(
    `Исключить ${person.value.name} из класса? Роли будут сняты. ` +
      'История взносов сохранится, в новые сборы человек не попадёт.',
  )
  if (!ok) return
  busy.value = true
  try {
    await api.personLeaves(person.value.id)
    haptic('success')
    toast('Исключён из класса')
    await refreshHome()
    router.replace('/people')
  } catch (e) {
    await alertDialog((e as Error).message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <div v-if="error" class="empty danger-text">{{ error }}</div>
    <div v-else-if="!person" class="spinner" />

    <template v-else>
      <h1 class="page-title">{{ person.name }}<span v-if="isMe" class="muted"> (вы)</span></h1>
      <p class="page-subtitle">
        <template v-if="!person.in_telegram">Без Telegram — взносы отмечаются вручную</template>
        <template v-else-if="!person.bot_connected">
          Бот не подключён — напоминания не доходят
        </template>
        <template v-else-if="person.username">@{{ person.username }}</template>
        <template v-else>Бот подключён</template>
      </p>

      <form class="section" @submit.prevent="saveNames">
        <div class="section-title">Данные</div>
        <div class="card">
          <label class="field">
            <span class="field-label">Как записан</span>
            <input v-model="name" maxlength="64" required />
          </label>
          <label class="field">
            <span class="field-label">Имя ребёнка</span>
            <input v-model="child" maxlength="64" placeholder="не указано" />
          </label>
        </div>
        <p class="section-note">Только имя ребёнка, без фамилии.</p>
        <button v-if="dirty" class="btn" style="margin-top: 12px" :disabled="busy">
          Сохранить
        </button>
      </form>

      <div v-if="can('role.grant')" class="section">
        <div class="section-title">Роли</div>
        <div class="card">
          <div v-for="r in ROLES" :key="r.role" class="row">
            <div class="row-main">
              <div>{{ r.title }}</div>
              <div class="row-sub">{{ r.hint }}</div>
            </div>
            <ToggleSwitch
              :model-value="person.roles.includes(r.role)"
              :disabled="busy"
              @update:model-value="(v) => toggleRole(r.role, v)"
            />
          </div>
        </div>
        <p class="section-note">
          Расход создаёт казначей, а утверждает председатель или ревизор — чтобы деньги
          не проводил один человек.
        </p>
      </div>

      <div v-if="!isMe" class="section">
        <button class="btn btn-danger" type="button" :disabled="busy" @click="removeFromClass">
          Исключить из класса
        </button>
      </div>
    </template>
  </div>
</template>
