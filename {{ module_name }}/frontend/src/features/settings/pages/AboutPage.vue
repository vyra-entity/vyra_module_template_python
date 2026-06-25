<template>
  <div class="settings-page">
    <div class="page-header">
      <i class="pi pi-info-circle text-primary" />
      <div>
        <h2 class="m-0">About</h2>
        <p class="text-sm text-color-secondary m-0">Modulinformationen und Lizenzen</p>
      </div>
    </div>

    <div class="grid">
      <div class="col-12 md:col-6">
        <Card>
          <template #title>
            <div class="flex align-items-center gap-2">
              <i class="pi pi-box" />
              <span>Modul</span>
            </div>
          </template>
          <template #content>
            <div v-if="loading" class="text-sm text-color-secondary">Lade Modulinformationen…</div>
            <div v-else-if="error" class="text-sm text-orange-500">{{ error }}</div>
            <div v-else class="flex flex-column gap-3">
              <div>
                <label class="block mb-1 font-semibold text-sm">Modulname</label>
                <span>{{ displayName }}</span>
              </div>
              <div>
                <label class="block mb-1 font-semibold text-sm">Version</label>
                <span>{{ versionLabel }}</span>
              </div>
              <div>
                <label class="block mb-1 font-semibold text-sm">Beschreibung</label>
                <p class="m-0 text-sm text-color-secondary">{{ descriptionLabel }}</p>
              </div>
            </div>
          </template>
        </Card>
      </div>

      <div class="col-12 md:col-6">
        <Card>
          <template #title>
            <div class="flex align-items-center gap-2">
              <i class="pi pi-file" />
              <span>Lizenzen</span>
            </div>
          </template>
          <template #content>
            <p class="m-0 text-sm text-color-secondary">
              Lizenzinformationen und Hinweise zu Drittanbieter-Komponenten werden hier in einer zukünftigen Version angezeigt.
            </p>
          </template>
        </Card>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import Card from 'primevue/card'
import apiClient from '../../../api/http'
import { settingsApi, type ModuleAboutInfo } from '../../../api/settings.api'

const loading = ref(true)
const error = ref<string | null>(null)
const moduleInfo = ref<ModuleAboutInfo | null>(null)

const fallbackModuleName = computed(() =>
  (apiClient.defaults.baseURL ?? '').replace(/\/api$/, '').replace(/^\//, '') || '—'
)

const displayName = computed(() =>
  moduleInfo.value?.display_name || moduleInfo.value?.name || fallbackModuleName.value
)

const versionLabel = computed(() => moduleInfo.value?.version || '—')

const descriptionLabel = computed(() =>
  moduleInfo.value?.description || 'Keine Beschreibung verfügbar.'
)

onMounted(async () => {
  try {
    const response = await settingsApi.getModuleAbout()
    moduleInfo.value = response.module
  } catch (e) {
    error.value = e instanceof Error ? e.message : 'Modulinformationen konnten nicht geladen werden.'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.settings-page {
  padding: 2rem;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 2rem;
}

.page-header i {
  font-size: 1.5rem;
}
</style>
