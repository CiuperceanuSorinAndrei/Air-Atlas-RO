import { readFileSync } from 'node:fs'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), {
    name: 'public-observation-snapshot',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'observations.json', source: readFileSync('src/data/observations.json', 'utf8') })
    },
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        if (request.url?.split('?')[0]?.endsWith('/observations.json')) {
          response.setHeader('Content-Type', 'application/json')
          response.setHeader('Cache-Control', 'no-store')
          response.end(readFileSync('src/data/observations.json', 'utf8'))
        } else next()
      })
    },
  }],
  base: '/Air-Atlas-RO/',
})
