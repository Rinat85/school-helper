/**
 * Все запросы к бэкенду. Авторизация — подписанная initData из Telegram;
 * роли сервер берёт из своей БД, поэтому ничему из клиента он не верит.
 */
import { initData } from './telegram'
import type {
  Collection,
  CollectionDetail,
  Home,
  Invite,
  Me,
  MoneySummary,
  PendingPayment,
  Person,
  Settings,
} from './types'

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message)
  }
}

function authorization(): string {
  const data = initData()
  if (data) return `tma ${data}`
  // Локально в браузере: бэкенд пускает только с localhost и только при DEV_AUTH_TG_ID.
  if (import.meta.env.DEV) return 'dev'
  return ''
}

interface Options {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  headers?: Record<string, string>
}

async function detail(response: Response): Promise<string> {
  try {
    const json = await response.json()
    if (typeof json.detail === 'string') return json.detail
    if (Array.isArray(json.detail)) {
      return json.detail.map((d: { msg?: string }) => d.msg ?? '').join('; ')
    }
  } catch {
    /* тело не JSON */
  }
  return response.statusText || `ошибка ${response.status}`
}

async function request<T>(path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: authorization(), ...options.headers }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(`/api${path}`, {
    method: options.method ?? 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  })
  if (!response.ok) throw new ApiError(response.status, await detail(response))
  return (await response.json()) as T
}

/**
 * Один ключ на одно действие: если тот же запрос уйдёт повторно (обрыв связи,
 * ретрай браузера), сервер не задвоит деньги. От двойного тапа по кнопке
 * защищает флаг busy во вьюхе — второе нажатие просто не отправляется.
 */
function idempotencyKey(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
}

export const api = {
  me: () => request<Me>('/me'),
  home: () => request<Home>('/home'),
  summary: () => request<MoneySummary>('/money/summary'),

  // Люди
  people: () => request<Person[]>('/people'),
  addPerson: (name: string, child: string | null) =>
    request<Person>('/people', { method: 'POST', body: { name, child } }),
  editPerson: (id: number, name: string, child: string | null) =>
    request<Person>(`/people/${id}`, { method: 'PATCH', body: { name, child } }),
  approvePerson: (id: number) =>
    request<Person & { enrolled: string[] }>(`/people/${id}/approve`, { method: 'POST' }),
  declinePerson: (id: number) =>
    request<{ ok: true }>(`/people/${id}/decline`, { method: 'POST' }),
  personLeaves: (id: number) => request<{ ok: true }>(`/people/${id}/leave`, { method: 'POST' }),
  setRole: (id: number, role: string, enabled: boolean) =>
    request<Person>(`/people/${id}/roles/${role}`, { method: 'PUT', body: { enabled } }),
  invite: () => request<Invite>('/invite'),

  // Настройки
  settings: () => request<Settings>('/settings'),
  saveSettings: (patch: Partial<Settings>) =>
    request<Settings>('/settings', { method: 'PUT', body: patch }),

  // Сборы
  collections: () => request<Collection[]>('/collections'),
  collection: (id: number) => request<CollectionDetail>(`/collections/${id}`),
  createCollection: (body: {
    title: string
    amount_per_person: number
    due_date: string | null
    purpose: string | null
  }) =>
    request<Collection & { posted_to_group: boolean; queued: number }>('/collections', {
      method: 'POST',
      body,
    }),
  closeCollection: (id: number) =>
    request<Collection>(`/collections/${id}/close`, { method: 'POST' }),
  setWaived: (id: number, personId: number, waived: boolean) =>
    request<Collection>(`/collections/${id}/people/${personId}/waive`, {
      method: 'PUT',
      body: { waived },
    }),
  recordPayment: (id: number, personId: number, method: 'cash' | 'transfer', amount: number) =>
    request<Collection>(`/collections/${id}/payments`, {
      method: 'POST',
      body: { person_id: personId, method, amount },
      headers: { 'Idempotency-Key': idempotencyKey() },
    }),

  // Платежи
  pending: () => request<PendingPayment[]>('/payments/pending'),
  confirmPayment: (id: number) =>
    request<{ status: string }>(`/payments/${id}/confirm`, { method: 'POST' }),
  rejectPayment: (id: number) =>
    request<{ status: string }>(`/payments/${id}/reject`, { method: 'POST', body: {} }),

  /** Чек через бэкенд: <img src> не умеет слать заголовок авторизации. */
  async fileUrl(id: number): Promise<string> {
    const response = await fetch(`/api/files/${id}`, {
      headers: { Authorization: authorization() },
    })
    if (!response.ok) throw new ApiError(response.status, await detail(response))
    return URL.createObjectURL(await response.blob())
  },
}
