import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
import { setup } from './telegram'
import './styles.css'

setup()
createApp(App).use(router).mount('#app')
