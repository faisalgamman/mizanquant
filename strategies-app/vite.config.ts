import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/static/strategies-app/',
  build: {
    outDir: '../app/static/strategies-app',
    emptyOutDir: true,
  },
})
