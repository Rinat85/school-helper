import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
import { alertDialog, setup } from './telegram'
import './styles.css'

setup()
const app = createApp(App)

/* Ни одна ошибка не должна выглядеть как «кнопка не нажимается»: всё, что
   не поймали экраны, показываем человеку. Повторы подряд не спамим. */
let lastShown = 0

function report(error: unknown): void {
  console.error(error)
  if (Date.now() - lastShown < 3000) return
  lastShown = Date.now()
  const text = error instanceof Error ? error.message : String(error)
  void alertDialog(`Что-то пошло не так: ${text}\n\nЗакройте приложение и откройте снова.`)
}

app.config.errorHandler = (error) => report(error)
window.addEventListener('unhandledrejection', (event) => report(event.reason))

app.use(router).mount('#app')
