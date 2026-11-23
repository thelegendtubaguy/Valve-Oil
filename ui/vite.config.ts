/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
// The installer bundle passes the package version; local builds use package.json's.
const appVersion: string = process.env.VO_UI_VERSION || pkg.version
const statusTarget = process.env.VO_PRINTER || 'http://localhost:7125'

// Relative base so the build works from any sub-path the web server mounts it on.
export default defineConfig({
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(appVersion),
  },
  plugins: [
    react(),
    tailwindcss(),
    {
      // version.json lets open tabs notice a newly installed build (src/lib/update-check.ts).
      name: 'valve-oil-version',
      generateBundle() {
        this.emitFile({ type: 'asset', fileName: 'version.json', source: JSON.stringify({ version: appVersion }) + '\n' })
      },
    },
    {
      // Vite lists the licenses of bundled scripts only. Montserrat arrives through CSS, and the SIL Open Font License
      // asks for its text to go with the font, so it's added to licenses.md here.
      name: 'valve-oil-font-license',
      apply: 'build',
      writeBundle(options) {
        const fontPkg = JSON.parse(readFileSync(new URL('./node_modules/@fontsource-variable/montserrat/package.json', import.meta.url), 'utf8'))
        const text = readFileSync(new URL('./node_modules/@fontsource-variable/montserrat/LICENSE', import.meta.url), 'utf8')
        appendFileSync(join(options.dir!, 'licenses.md'), `\n## ${fontPkg.name} - ${fontPkg.version} (${fontPkg.license})\n\nThe Montserrat typeface.\n\n\`\`\`\n${text.trim()}\n\`\`\`\n`)
      },
    },
    {
      // The printer's nginx has gzip_static on: ship a .gz beside each script and stylesheet so the RK3308 doesn't
      // compress them on every request. gzipSync writes no timestamp, so the archive stays reproducible.
      name: 'valve-oil-gzip',
      apply: 'build',
      writeBundle(options, bundle) {
        for (const file of Object.keys(bundle)) {
          if (!/\.(js|css|svg)$/.test(file)) continue
          const path = join(options.dir!, file)
          writeFileSync(`${path}.gz`, gzipSync(readFileSync(path), { level: 9 }))
        }
      },
    },
  ],
  server: {
    proxy: {
      // Valve Oil's status service, which the printer's nginx serves on the same origin as Valve Oil UI. VO_PRINTER
      // names the printer or fake Moonraker to send it to; Origin is rewritten to match, as the service refuses
      // cross-origin changes.
      '/vo-status': {
        target: statusTarget,
        changeOrigin: true,
        configure: (proxy) => proxy.on('proxyReq', (req) => req.getHeader('origin') && req.setHeader('origin', new URL(statusTarget).origin)),
      },
      // The touchscreen stream (vo-remote-screen), a websocket on the same origin.
      '/vo-screen': { target: statusTarget, ws: true, changeOrigin: true },
      // Valve Oil's add-ons service, on the same origin and with the same cross-origin rule.
      '/vo-addons': {
        target: statusTarget,
        changeOrigin: true,
        configure: (proxy) => proxy.on('proxyReq', (req) => req.getHeader('origin') && req.setHeader('origin', new URL(statusTarget).origin)),
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 800,
    // Licenses of the bundled npm packages (React, lucide), shipped with the build.
    license: { fileName: 'licenses.md' },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
  },
})
