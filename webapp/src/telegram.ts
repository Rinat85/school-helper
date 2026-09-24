/**
 * Тонкая обёртка над Telegram.WebApp.
 *
 * Вне Telegram (локальная разработка в браузере) объект либо отсутствует,
 * либо есть, но с пустой initData — тогда всё деградирует до браузерных
 * аналогов: confirm(), alert(), без кнопки «Назад» и вибрации.
 */

interface BackButton {
  show(): void
  hide(): void
  onClick(cb: () => void): void
  offClick(cb: () => void): void
}

interface WebApp {
  initData: string
  version: string
  colorScheme: 'light' | 'dark'
  ready(): void
  expand(): void
  isVersionAtLeast(version: string): boolean
  setHeaderColor?(color: string): void
  setBackgroundColor?(color: string): void
  showConfirm?(message: string, cb: (ok: boolean) => void): void
  showAlert?(message: string, cb?: () => void): void
  openTelegramLink?(url: string): void
  BackButton?: BackButton
  HapticFeedback?: {
    notificationOccurred(type: 'success' | 'error' | 'warning'): void
    impactOccurred(style: 'light' | 'medium' | 'heavy'): void
    selectionChanged(): void
  }
}

const raw = (window as unknown as { Telegram?: { WebApp?: WebApp } }).Telegram?.WebApp

/** WebApp, только если мы действительно внутри Telegram. */
export const tg: WebApp | undefined = raw && raw.initData ? raw : undefined

export const insideTelegram = tg !== undefined

export function initData(): string {
  return tg?.initData ?? ''
}

export function setup(): void {
  if (!tg) return
  tg.ready()
  tg.expand()
  if (tg.isVersionAtLeast('6.1')) {
    tg.setHeaderColor?.('secondary_bg_color')
    tg.setBackgroundColor?.('secondary_bg_color')
  }
}

type Haptic = 'success' | 'error' | 'warning' | 'tap'

export function haptic(kind: Haptic): void {
  const h = tg?.isVersionAtLeast('6.1') ? tg.HapticFeedback : undefined
  if (!h) return
  if (kind === 'tap') h.selectionChanged()
  else h.notificationOccurred(kind)
}

/* Всплывающие окна Telegram бросают исключение, если текст длиннее 256 символов
   или если другое окно ещё открыто. Непойманное исключение выглядело бы так,
   будто кнопка просто не нажимается, — поэтому обрезаем текст и при любой
   ошибке падаем на браузерный confirm/alert. */
const POPUP_LIMIT = 256

function clip(text: string): string {
  return text.length > POPUP_LIMIT ? `${text.slice(0, POPUP_LIMIT - 1)}…` : text
}

export function confirmDialog(message: string): Promise<boolean> {
  if (tg?.showConfirm && tg.isVersionAtLeast('6.2')) {
    return new Promise((resolve) => {
      try {
        tg.showConfirm!(clip(message), resolve)
      } catch {
        resolve(window.confirm(message))
      }
    })
  }
  return Promise.resolve(window.confirm(message))
}

export function alertDialog(message: string): Promise<void> {
  if (tg?.showAlert && tg.isVersionAtLeast('6.2')) {
    return new Promise((resolve) => {
      try {
        tg.showAlert!(clip(message), () => resolve())
      } catch {
        window.alert(message)
        resolve()
      }
    })
  }
  window.alert(message)
  return Promise.resolve()
}

/** Системная кнопка «Назад» в шапке Telegram — привычнее, чем своя стрелка. */
let backHandler: (() => void) | null = null

export function setBackButton(handler: (() => void) | null): void {
  const button = tg?.isVersionAtLeast('6.1') ? tg.BackButton : undefined
  if (!button) return
  if (backHandler) button.offClick(backHandler)
  backHandler = handler
  if (handler) {
    button.onClick(handler)
    button.show()
  } else {
    button.hide()
  }
}

export function shareLink(url: string, text: string): void {
  const share = `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(text)}`
  if (tg?.openTelegramLink) tg.openTelegramLink(share)
  else window.open(share, '_blank')
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
