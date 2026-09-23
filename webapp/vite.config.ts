import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  base: './',
  build: {
    // FastAPI отдаёт статику из web/ — тот же домен, что и API, без CORS.
    outDir: '../web',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    // Запросы от прокси приходят на бэкенд с 127.0.0.1 — поэтому dev-вход
    // (DEV_AUTH_TG_ID) работает локально и не работает на сервере.
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
})
