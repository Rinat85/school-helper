/** Короткое подтверждение внизу экрана: «Записал в кассу», «Сбор объявлен». */
import { ref } from 'vue'

export const toastText = ref('')
let timer: ReturnType<typeof setTimeout> | undefined

export function toast(text: string, ms = 2600): void {
  toastText.value = text
  clearTimeout(timer)
  timer = setTimeout(() => (toastText.value = ''), ms)
}
