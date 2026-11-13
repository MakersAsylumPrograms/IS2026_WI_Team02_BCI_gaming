import {resolve} from 'node:path';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// STANDALONE DEMO of the React + Tailwind redesign. Not connected to the
// backend: src/lib/demoApi.js fakes it in the browser.
// `npm run dev` -> http://localhost:5173 (landing), /patient/, /staff/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    // The backend's Content-Security-Policy is `default-src 'self'`, which
    // blocks data: URLs. Never inline fonts/images; always emit real files.
    assetsInlineLimit: 0,
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        patient: resolve(import.meta.dirname, 'patient/index.html'),
        staff: resolve(import.meta.dirname, 'staff/index.html'),
      },
    },
  },
  server: {port: 5173, strictPort: true},
});
