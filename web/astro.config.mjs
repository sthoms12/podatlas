import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  site: 'https://podatlas.app',
  output: 'static',
  vite: {
    plugins: [tailwindcss()],
  },
});
