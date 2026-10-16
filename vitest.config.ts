import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    projects: [
      {
        plugins: [react()],
        test: {
          name: 'control-plane',
          globals: true,
          environment: 'jsdom',
          setupFiles: ['./frontend/control-plane/src/test/setup.ts'],
          include: ['frontend/control-plane/src/**/*.test.{ts,tsx}'],
          clearMocks: true,
        },
      },
      {
        plugins: [react()],
        test: {
          name: 'website',
          globals: true,
          environment: 'jsdom',
          setupFiles: ['./frontend/website/src/test/setup.ts'],
          include: ['frontend/website/src/**/*.test.{ts,tsx}'],
          clearMocks: true,
        },
      },
    ],
  },
});
