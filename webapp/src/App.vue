<script setup lang="ts">
import { onMounted } from 'vue'

import TabBar from './components/TabBar.vue'
import { loadSession, session } from './session'
import { toastText } from './toast'

onMounted(loadSession)
</script>

<template>
  <div v-if="session.loading" class="spinner" />

  <div v-else-if="session.blocked" class="page">
    <div class="empty">
      <div style="font-size: 44px; margin-bottom: 8px">🏫</div>
      {{ session.blocked }}
    </div>
  </div>

  <template v-else>
    <RouterView v-slot="{ Component }">
      <component :is="Component" />
    </RouterView>
    <TabBar />
  </template>

  <div v-if="toastText" class="toast">{{ toastText }}</div>
</template>
