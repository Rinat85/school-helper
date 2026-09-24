import { createRouter, createWebHashHistory } from 'vue-router'

import { alertDialog, setBackButton } from './telegram'

/*
 * Hash-история: статику отдаёт FastAPI, и ему не нужно знать про клиентские
 * маршруты — любая ссылка открывается с index.html.
 *
 * meta.back: экран «второго уровня» — в шапке Telegram показывается «Назад».
 */
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
    {
      path: '/collections',
      name: 'collections',
      component: () => import('./views/CollectionsView.vue'),
    },
    {
      path: '/collections/new',
      name: 'collection-new',
      component: () => import('./views/CollectionNewView.vue'),
      meta: { back: true },
    },
    {
      path: '/collections/:id',
      name: 'collection',
      component: () => import('./views/CollectionView.vue'),
      props: (route) => ({ id: Number(route.params.id) }),
      meta: { back: true },
    },
    { path: '/payments', name: 'payments', component: () => import('./views/PaymentsView.vue') },
    { path: '/people', name: 'people', component: () => import('./views/PeopleView.vue') },
    {
      path: '/people/:id',
      name: 'person',
      component: () => import('./views/PersonView.vue'),
      props: (route) => ({ id: Number(route.params.id) }),
      meta: { back: true },
    },
    { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

/*
 * Экраны грузятся отдельными файлами с хешем в имени, и каждая выкатка их
 * переименовывает. Если приложение было открыто до выкатки, переход на экран
 * запрашивает файл, которого на сервере уже нет, и vue-router молча отменяет
 * переход — «кнопка не нажимается». Лечится перезагрузкой: свежий index.html
 * знает новые имена. Флаг не даёт уйти в бесконечную перезагрузку.
 */
const RELOAD_FLAG = 'reloaded-for-new-version'
const CHUNK_ERROR =
  /dynamically imported module|Importing a module script failed|error loading dynamically|Failed to fetch/i

router.afterEach((to) => {
  setBackButton(to.meta.back ? () => router.back() : null)
  forget(RELOAD_FLAG)
})

router.onError((error, to) => {
  if (!CHUNK_ERROR.test(String((error as Error)?.message ?? error))) return
  if (recall(RELOAD_FLAG)) {
    // Уже перезагружались, а файл всё равно не грузится — значит, дело в связи.
    // RouterLink такие ошибки глушит, поэтому говорим сами.
    void alertDialog('Не удалось открыть экран. Проверьте интернет и откройте приложение заново.')
    return
  }
  remember(RELOAD_FLAG)
  window.location.hash = to.fullPath
  window.location.reload()
})

function remember(key: string): void {
  try {
    sessionStorage.setItem(key, '1')
  } catch {
    /* хранилище может быть недоступно — тогда просто без защиты от цикла */
  }
}

function recall(key: string): boolean {
  try {
    return sessionStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function forget(key: string): void {
  try {
    sessionStorage.removeItem(key)
  } catch {
    /* см. remember */
  }
}
