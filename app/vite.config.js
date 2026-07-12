import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Base path: '/' for local dev; the repo path for GitHub Pages builds
// (deploy.yml sets BASE_PATH=/<repo-name>/).
export default defineConfig(({ command }) => ({
  base: command === 'build' ? (process.env.BASE_PATH ?? '/daily-digest/') : '/',
  plugins: [react()],
}))
