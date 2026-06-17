// vite.config.js - Für Vite-basierte Vue.js Projekte
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  base: '/{{ module_name }}/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    port: 3000,
    host: '0.0.0.0',
    hmr: {
      clientPort: 3000,
      protocol: 'ws',
      host: 'localhost'
    },
    proxy: {
      '/{{ module_name }}/api': {
        target: 'http://{{ module_name }}:8443',
        changeOrigin: true,
        secure: false,
        rewrite: (path) => path.replace(/^\/{{ module_name }}/, '')
      },
      '/{{ module_name }}/ws': {
        target: 'ws://{{ module_name }}:8443',
        changeOrigin: true,
        secure: false,
        ws: true,
        rewrite: (path) => path.replace(/^\/{{ module_name }}/, '')
      }
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    // Optimiert für Nginx Static File Serving
    rollupOptions: {
      output: {
        assetFileNames: '[name]-[hash][extname]',
        chunkFileNames: '[name]-[hash].js',
        entryFileNames: '[name]-[hash].js'
      }
    },
    // Nginx-optimierte Einstellungen
    target: 'es2015',
    minify: 'esbuild', // Fallback zu esbuild wenn terser nicht verfügbar
    sourcemap: false, // Für Production
    reportCompressedSize: false,
    chunkSizeWarningLimit: 1000
  },
  // Optimierung für Nginx-Caching
  define: {
    __VUE_OPTIONS_API__: true,
    __VUE_PROD_DEVTOOLS__: false
  }
})
