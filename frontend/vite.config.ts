/// <reference types="vitest/config" />
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const BACKEND = 'http://127.0.0.1:1231';

// Every backend path the app calls. The Host header is kept (no changeOrigin) because the
// backend's loopback check compares the Origin port with the Host port.
const API_PATHS = [
  '/auth-status', '/login', '/logout', '/healthz', '/logo.png', '/static',
  '/api', '/list-files', '/select-file', '/preview-excel', '/summary-dashboard', '/download-excel',
  '/upload-excel', '/source-preferences', '/sync-google-sheet', '/push-google-sheet',
  '/google-oauth-status', '/google-oauth-client', '/google-service-account', '/google-oauth-login',
  '/google-oauth-complete', '/proxy-list', '/proxy-test', '/scrape-history', '/report-partners',
  '/export-report', '/threads-session', '/tiktok-session', '/riviu-data', '/scan-settings', '/schedule',
];

export default defineConfig({
  base: '/app/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  build: {
    outDir: '../riviu/web/app',
    emptyOutDir: true,
    // One bundle (~195 KB gzip) for an internal tool; splitting buys little here.
    chunkSizeWarningLimit: 800,
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      // GET / on the backend issues the per-process session cookie in source mode.
      '^/$': { target: BACKEND },
      ...Object.fromEntries(API_PATHS.map(prefix => [prefix, { target: BACKEND }])),
      '/ws': { target: BACKEND.replace('http', 'ws'), ws: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
    // Rendering the whole layout in jsdom can take a few seconds on slow machines.
    testTimeout: 20000,
  },
});
