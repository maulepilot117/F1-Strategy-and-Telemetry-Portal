import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
// tailwindcss() must come before react() so Tailwind processes CSS first
export default defineConfig({
  plugins: [tailwindcss(), react()],
})
