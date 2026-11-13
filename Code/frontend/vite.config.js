import {resolve} from 'node:path';
import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// The EqualCare patient and staff screens (React + Tailwind).
// `npm run build` -> dist/, which the backend (Code/backend/app.py) serves at /, /patient/, /staff/.
// `npm run dev`   -> http://localhost:5173 with live reload; API calls go to the backend on port 8000.
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
  server: {port: 5173, strictPort: true, proxy: {'/api': 'http://127.0.0.1:8000'}},
});
