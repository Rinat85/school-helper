<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '../api'
import { plural, ROLE_NAMES } from '../format'
import { refreshHome, session } from '../session'
import { alertDialog, confirmDialog, copyText, haptic, shareLink } from '../telegram'
import { toast } from '../toast'
import type { Invite, Person } from '../types'

const people = ref<Person[] | null>(null)
const invite = ref<Invite | null>(null)
const error = ref('')

const adding = ref(false)
const newName = ref('')
const newChild = ref('')
const saving = ref(false)

async function load(): Promise<void> {
  try {
    ;[people.value, invite.value] = await Promise.all([api.people(), api.invite()])
  } catch (e) {
    error.value = (e as Error).message
  }
}

onMounted(load)

const inviteText = computed(
  () => `Подключитесь к боту класса ${session.me?.class.name ?? ''} — сборы, голосования и напоминания`,
)

async function copyInvite(): Promise<void> {
  if (!invite.value) return
  if (await copyText(invite.value.link)) {
    haptic('success')
    toast('Ссылка скопирована')
  } else {
    await alertDialog(invite.value.link)
  }
}

function shareInvite(): void {
  if (invite.value) shareLink(invite.value.link, inviteText.value)
}

async function addPerson(): Promise<void> {
  if (!newName.value.trim() || saving.value) return
  saving.value = true
  try {
    await api.addPerson(newName.value.trim(), newChild.value.trim() || null)
    haptic('success')
    toast('Добавлен. Взносы за него отмечайте вручную')
    newName.value = ''
    newChild.value = ''
    adding.value = false
    await load()
  } catch (e) {
    await alertDialog((e as Error).message)
  } finally {
    saving.value = false
  }
}

/* Пришедшие по ссылке и ещё не впущенные — отдельно и сверху: это решение,
   которое ждёт председателя. В общем составе их нет. */
const pending = computed(() => people.value?.filter((p) => !p.approved) ?? [])
const members = computed(() => people.value?.filter((p) => p.approved) ?? [])
const deciding = ref<number | null>(null)

async function decide(p: Person, approve: boolean): Promise<void> {
  if (deciding.value !== null) return
  if (!approve) {
    const ok = await confirmDialog(
      `Отклонить заявку ${p.name}? Человек не попадёт в класс, но сможет подать заявку снова.`,
    )
    if (!ok) return
  }
  deciding.value = p.id
  try {
    if (approve) {
      const result = await api.approvePerson(p.id)
      haptic('success')
      toast(
        result.enrolled.length
          ? `${p.name} в классе и в идущих сборах: ${result.enrolled.join(', ')}`
          : `${p.name} в классе`,
      )
    } else {
      await api.declinePerson(p.id)
      haptic('warning')
      toast('Заявка отклонена')
    }
    await load()
    await refreshHome()
  } catch (e) {
    haptic('error')
    await alertDialog((e as Error).message)
    await load()
  } finally {
    deciding.value = null
  }
}

function subtitle(p: Person): string {
  const parts: string[] = []
  if (p.child) parts.push(p.child)
  if (!p.in_telegram) parts.push('без Telegram')
  else if (!p.bot_connected) parts.push('бот не подключён')
  return parts.join(' · ')
}

onMounted(refreshHome)
</script>

<template>
  <div class="page">
    <h1 class="page-title">Люди</h1>
    <p class="page-subtitle">
      <template v-if="people">
        {{ members.length }} {{ plural(members.length, 'человек', 'человека', 'человек') }} в классе
      </template>
    </p>

    <div v-if="error" class="empty danger-text">{{ error }}</div>

    <template v-else>
      <div v-if="pending.length" class="section">
        <div class="section-title">Ждут подтверждения · {{ pending.length }}</div>
        <div class="card">
          <template v-for="p in pending" :key="p.id">
            <div class="row">
              <div class="row-main">
                <div class="row-title">{{ p.name }}</div>
                <div class="row-sub">
                  <template v-if="p.child">{{ p.child }} · </template>
                  <template v-if="p.username">@{{ p.username }} · </template>
                  пришёл(а) по ссылке
                </div>
              </div>
            </div>
            <div class="btn-row" style="padding: 0 16px 14px">
              <button class="btn btn-small" :disabled="deciding !== null" @click="decide(p, true)">
                Впустить
              </button>
              <button
                class="btn btn-small btn-danger"
                :disabled="deciding !== null"
                @click="decide(p, false)"
              >
                Отклонить
              </button>
            </div>
          </template>
        </div>
        <p class="section-note">
          Пока вы не впустили человека, он не видит кассу, не получает рассылок и не
          участвует в сборах. После одобрения попадёт и в уже идущие сборы.
        </p>
      </div>

      <div class="section">
        <div class="section-title">Пригласить родителей</div>
        <div class="card card-pad stack">
          <div class="muted" style="font-size: 14px">
            По этой ссылке родитель попадает в список класса и начинает получать
            напоминания. Разошлите её в чат или лично.
          </div>
          <div class="btn-row">
            <button class="btn" type="button" @click="shareInvite">Отправить</button>
            <button class="btn btn-secondary" type="button" @click="copyInvite">
              Скопировать
            </button>
          </div>
        </div>
        <p v-if="invite?.not_connected.length" class="section-note">
          Не подключили бота:
          {{ invite.not_connected.map((p) => p.name).join(', ') }}
        </p>
      </div>

      <div v-if="!people" class="spinner" />

      <div v-else class="section">
        <div class="section-title">Состав</div>
        <div class="card">
          <RouterLink
            v-for="p in members"
            :key="p.id"
            :to="`/people/${p.id}`"
            class="row chevron"
          >
            <div class="row-main">
              <div class="row-title">
                {{ p.name }}
                <span v-if="p.id === session.me?.person.id" class="muted"> (вы)</span>
              </div>
              <div v-if="subtitle(p) || p.roles.length" class="row-sub">
                <span v-for="r in p.roles" :key="r" class="chip chip-accent">
                  {{ ROLE_NAMES[r] }}
                </span>
                {{ subtitle(p) }}
              </div>
            </div>
            <span v-if="p.in_telegram && !p.bot_connected" title="Бот не подключён">📵</span>
          </RouterLink>
        </div>
      </div>

      <div class="section">
        <button v-if="!adding" class="btn btn-secondary" type="button" @click="adding = true">
          ＋ Добавить родителя без Telegram
        </button>

        <form v-else @submit.prevent="addPerson">
          <div class="section-title">Родитель без Telegram</div>
          <div class="card">
            <label class="field">
              <span class="field-label">Имя</span>
              <input v-model="newName" maxlength="64" placeholder="Ольга" required />
            </label>
            <label class="field">
              <span class="field-label">Имя ребёнка (необязательно)</span>
              <input v-model="newChild" maxlength="64" placeholder="Ваня" />
            </label>
          </div>
          <p class="section-note">
            Бот не сможет ему писать. Взносы за такого родителя казначей отмечает вручную.
          </p>
          <div class="btn-row" style="margin-top: 12px">
            <button class="btn" type="submit" :disabled="!newName.trim() || saving">
              Добавить
            </button>
            <button class="btn btn-secondary" type="button" @click="adding = false">
              Отмена
            </button>
          </div>
        </form>
      </div>
    </template>
  </div>
</template>
