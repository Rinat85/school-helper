/** Русские форматы. Совпадают с тем, что пишет бот (src/schoolhelper/core/util.py). */

const MONTHS = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
]

/** 1200000 -> «1 200 000» с неразрывными пробелами. */
export function money(amount: number, currency = false): string {
  const text = Math.round(amount)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return currency ? `${text} сум` : text
}

/** '2026-12-15' или ISO-метка -> «15 декабря». */
export function dateRu(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value.length === 10 ? `${value}T12:00:00` : value)
  if (Number.isNaN(date.getTime())) return value
  const sameYear = date.getFullYear() === new Date().getFullYear()
  return `${date.getDate()} ${MONTHS[date.getMonth()]}${sameYear ? '' : ` ${date.getFullYear()}`}`
}

export function plural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n) % 100
  if (abs >= 11 && abs <= 14) return many
  const last = abs % 10
  if (last === 1) return one
  if (last >= 2 && last <= 4) return few
  return many
}

export const ROLE_NAMES: Record<string, string> = {
  parent: 'родитель',
  treasurer: 'казначей',
  chair: 'председатель',
  auditor: 'ревизор',
  teacher: 'учитель',
  admin: 'админ',
}

export const STATUS_NAMES: Record<string, string> = {
  confirmed: 'сдал',
  partial: 'частично',
  claimed: 'ждёт подтверждения',
  waived: 'освобождён',
  pending: 'не сдал',
}

/** Только цифры: «50 000» -> 50000. */
export function parseAmount(text: string): number {
  const digits = text.replace(/\D/g, '')
  return digits ? Number(digits) : 0
}
