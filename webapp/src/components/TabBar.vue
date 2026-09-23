<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { can, session } from '../session'
import { haptic } from '../telegram'

interface Tab {
  to: string
  label: string
  icon: string
  badge?: number
}

/* Показываем только разделы, которые человеку доступны. Учителю, например,
   денежные вкладки не видны вообще — как и в боте (SPEC §3.2). */
const tabs = computed<Tab[]>(() => {
  const home = session.home
  const list: Tab[] = [{ to: '/', label: 'Главная', icon: 'home' }]
  if (session.me?.sees_money) list.push({ to: '/collections', label: 'Сборы', icon: 'coins' })
  if (can('payment.confirm')) {
    list.push({ to: '/payments', label: 'Платежи', icon: 'check', badge: home?.payments_to_confirm })
  }
  if (can('person.manage')) {
    list.push({ to: '/people', label: 'Люди', icon: 'people', badge: home?.not_connected })
  }
  if (can('class.edit') || can('collection.create')) {
    list.push({ to: '/settings', label: 'Класс', icon: 'gear' })
  }
  return list
})

const route = useRoute()

function active(to: string): boolean {
  return to === '/' ? route.path === '/' : route.path.startsWith(to)
}

const ICONS: Record<string, string> = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z',
  coins:
    'M12 3c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zm8 6c0 1.7-3.6 3-8 3s-8-1.3-8-3m16 3c0 1.7-3.6 3-8 3s-8-1.3-8-3m16 3c0 1.7-3.6 3-8 3s-8-1.3-8-3M4 6v12m16-12v12',
  check: 'M4 12.5 9.5 18 20 6',
  people:
    'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm-7 10a7 7 0 0 1 14 0M17 11a3 3 0 1 0 0-6m2 16a6 6 0 0 0-3-5.2',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.2l2-1.6-2-3.4-2.4 1a7.3 7.3 0 0 0-2-1.2L14.5 3h-5l-.4 2.6a7.3 7.3 0 0 0-2 1.2l-2.4-1-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.4l-2 1.6 2 3.4 2.4-1a7.3 7.3 0 0 0 2 1.2l.4 2.6h5l.4-2.6a7.3 7.3 0 0 0 2-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z',
}
</script>

<template>
  <nav v-if="tabs.length > 1" class="tabbar">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.to"
      :to="tab.to"
      class="tab"
      :class="{ active: active(tab.to) }"
      @click="haptic('tap')"
    >
      <span class="icon-wrap">
        <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
          <path
            :d="ICONS[tab.icon]"
            fill="none"
            stroke="currentColor"
            stroke-width="1.8"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
        <span v-if="tab.badge" class="badge tab-badge">{{ tab.badge }}</span>
      </span>
      <span class="label">{{ tab.label }}</span>
    </RouterLink>
  </nav>
</template>

<style scoped>
.tabbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  height: calc(var(--tabbar) + var(--safe-bottom));
  padding-bottom: var(--safe-bottom);
  background: var(--section);
  border-top: 0.5px solid var(--separator);
  z-index: 10;
}

.tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  color: var(--hint);
  text-decoration: none;
  font-size: 11px;
}

.tab.active {
  color: var(--accent);
}

.icon-wrap {
  position: relative;
  display: flex;
}

.tab-badge {
  position: absolute;
  top: -4px;
  left: 16px;
  min-width: 18px;
  height: 18px;
  font-size: 11px;
}
</style>
