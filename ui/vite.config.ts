/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { gzipSync } from 'node:zlib'

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'))
// The installer bundle passes the package version; local builds use package.json's.
const appVersion: string = process.env.VO_UI_VERSION || pkg.version

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
