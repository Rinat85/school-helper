/**
 * Кто открыл приложение и что ему можно. Права здесь — только подсказка для
 * отрисовки: каждое действие сервер проверяет заново.
 */
import { reactive } from 'vue'

import { api, ApiError } from './api'
import type { Home, Me, Permission } from './types'

export const session = reactive({
  me: null as Me | null,
  home: null as Home | null,
  loading: true,
  /** Текст для экрана «не пустили»: не в списке класса, открыто вне Telegram и т.п. */
  blocked: '' as string,
})

export async function loadSession(): Promise<void> {
  session.loading = true
  try {
    session.me = await api.me()
    session.home = await api.home()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      session.blocked = 'Откройте приложение из Telegram — через кнопку в боте класса.'
    } else if (error instanceof ApiError && error.status === 403) {
      session.blocked = error.message.includes('одобрения')
        ? 'Ваша заявка ждёт подтверждения председателя класса. Как только он её рассмотрит, бот напишет.'
        : 'Вас пока нет в списке класса. Попросите председателя прислать ссылку-приглашение.'
    } else {
      session.blocked = 'Не удалось загрузиться. Проверьте связь и откройте приложение ещё раз.'
    }
  } finally {
    session.loading = false
  }
}

/** Обновить счётчики на вкладках после действия. */
export async function refreshHome(): Promise<void> {
  try {
    session.home = await api.home()
  } catch {
    /* счётчики не критичны */
  }
}

export function can(permission: Permission): boolean {
  return Boolean(session.me?.can[permission])
}
