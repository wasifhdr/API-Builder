import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3000,
    proxy: {
      // Anchored regexes (Vite treats a leading `^` as a RegExp source) —
      // a plain string key '/api' does a startsWith() match, which also
      // catches the unrelated client route /apis/:id and silently proxies
      // full-page navigations/reloads there to the backend instead of
      // letting Vite serve the SPA shell.
      '^/api(/|$)': { target: 'http://localhost:8000', ws: true },
      '^/v1(/|$)': { target: 'http://localhost:8000' },
    },
  },
})
