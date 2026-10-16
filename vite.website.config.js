import { resolve } from 'node:path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig(({ command }) => ({
  appType: 'mpa',
  base: command === 'build' ? '/FSQ/' : '/',
  root: resolve(import.meta.dirname, 'frontend/website'),
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    open: '/',
  },
  build: {
    outDir: resolve(import.meta.dirname, '.frontend-dist/website'),
    emptyOutDir: true,
    rollupOptions: {
      input: resolve(import.meta.dirname, 'frontend/website/index.html'),
    },
  },
}));
