import { createRouter, createWebHashHistory } from 'vue-router'

import { setBackButton } from './telegram'

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

router.afterEach((to) => {
  setBackButton(to.meta.back ? () => router.back() : null)
})
