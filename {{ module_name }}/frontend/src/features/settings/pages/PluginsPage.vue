<template>
  <div class="plugins-settings">
    <div class="plugins-settings__header">
      <div>
        <h2 class="plugins-settings__title">Plugins</h2>
        <p class="plugins-settings__subtitle">
          Getrennte Übersicht für modulzugeordnete Plugins und UI-Komponenten mit eigener Kommunikationszuordnung.
        </p>
      </div>
      <Button
        icon="pi pi-refresh"
        label="Neu laden"
        size="small"
        severity="secondary"
        outlined
        :loading="refreshing"
        @click="doRefresh"
      />
    </div>

    <div v-if="isLoading" class="plugins-settings__empty">
      <i class="pi pi-spin pi-spinner" style="font-size: 2rem; color: var(--text-color-secondary)" />
      <p>Plugin-Daten werden geladen…</p>
    </div>

    <div v-else-if="resolveError" class="plugins-settings__empty">
      <i class="pi pi-exclamation-triangle" style="font-size: 2rem; color: #f59e0b" />
      <p>Plugin-Daten konnten nicht geladen werden.</p>
      <p class="plugins-settings__empty-hint">{% raw %}{{ resolveError }}{% endraw %}</p>
    </div>

    <div v-else-if="pluginSections.length === 0" class="plugins-settings__empty">
      <i class="pi pi-puzzle" style="font-size: 2.5rem; color: var(--text-color-secondary); opacity: 0.4" />
      <p>Keine Plugins sichtbar.</p>
      <p class="plugins-settings__empty-hint">
        Installiere oder aktiviere Plugins im Module Manager.
      </p>
    </div>

    <div v-else class="plugins-settings__list">
      <section
        v-for="section in pluginSections"
        :key="section.id"
        class="plugins-section"
      >
        <div class="plugins-section__header">
          <div>
            <h3 class="plugins-section__title">{% raw %}{{ section.title }}{% endraw %}</h3>
            <p class="plugins-section__subtitle">{% raw %}{{ section.subtitle }}{% endraw %}</p>
          </div>
          <Tag :value="`${section.groups.length} Plugins`" severity="secondary" class="text-xs" />
        </div>

        <div class="plugins-section__list">
          <div
            v-for="plugin in section.groups"
            :key="`${section.id}-${plugin.pluginId}`"
            class="plugin-row"
          >
            <div class="plugin-row__header">
              <div class="plugin-row__icon">
                <img
                  v-if="plugin.icon && plugin.icon.startsWith('/')"
                  :src="plugin.icon"
                  :alt="plugin.title"
                  width="32"
                  height="32"
                />
                <i
                  v-else-if="plugin.icon"
                  :class="plugin.icon"
                  style="font-size: 1.5rem; color: var(--p-primary-color)"
                />
                <i
                  v-else
                  class="pi pi-puzzle"
                  style="font-size: 1.5rem; color: var(--p-primary-color)"
                />
              </div>
              <div class="plugin-row__meta">
                <div class="plugin-row__name">{% raw %}{{ plugin.title }}{% endraw %}</div>
                <div class="plugin-row__id">
                  <code>{% raw %}{{ plugin.pluginId }}{% endraw %}</code>
                  <Tag :value="`v${plugin.version}`" severity="secondary" class="text-xs" />
                  <Tag
                    :value="plugin.scopeType"
                    :severity="plugin.scopeType === 'GLOBAL' ? 'info' : 'secondary'"
                    class="text-xs"
                    v-tooltip.top="scopeLabel(plugin.scopeType, plugin.scopeTarget)"
                  />
                  <Tag
                    v-if="plugin.hasFrontendScope"
                    value="UI"
                    severity="success"
                    class="text-xs"
                    v-tooltip.top="'Frontend-Slot-Scope aktiv'"
                  />
                  <Tag
                    v-if="plugin.scopeTarget"
                    :value="plugin.scopeTarget"
                    severity="secondary"
                    class="text-xs"
                  />
                  <Tag
                    v-if="plugin.missingBindings > 0"
                    :value="`${plugin.missingBindings} offen`"
                    severity="warn"
                    class="text-xs"
                    v-tooltip.top="'Kommunikationsmodul noch nicht für alle relevanten UI-Komponenten gesetzt'"
                  />
                </div>
              </div>
            </div>

            <div class="plugin-row__slots">
              <div
                v-for="slot in plugin.slots"
                :key="slotRowKey(slot)"
                class="slot-row"
              >
                <div class="slot-row__info">
                  <i class="pi pi-window-minimize slot-row__icon" />
                  <div class="slot-row__ids">
                    <span
                      v-for="sid in uniqueSlotLabels(slot)"
                      :key="sid"
                      class="slot-row__label"
                    >{% raw %}{{ sid }}{% endraw %}</span>
                  </div>
                  <Tag
                    v-if="slot.slot_type"
                    :value="slot.slot_type"
                    severity="secondary"
                    class="text-xs"
                  />
                  <Tag
                    v-if="slot.priority !== undefined && slot.priority !== 50"
                    :value="`p:${slot.priority}`"
                    severity="secondary"
                    class="text-xs"
                    v-tooltip.top="'Priorität'"
                  />
                  <Tag
                    v-if="slot.min_user_role && slot.min_user_role !== 'operator'"
                    :value="slot.min_user_role"
                    severity="warn"
                    class="text-xs"
                    v-tooltip.top="'Mindest-Rolle'"
                  />
                  <Tag
                    v-if="slot.communication_module_name"
                    :value="`Kommunikation: ${slot.communication_module_name}`"
                    severity="contrast"
                    class="text-xs"
                  />
                  <Tag
                    v-else-if="requiresCommunicationBinding(slot)"
                    value="Kommunikation offen"
                    severity="warn"
                    class="text-xs"
                  />
                </div>
                <div class="slot-row__actions">
                  <span class="slot-row__status" :class="slot.is_active ? 'slot-row__status--active' : 'slot-row__status--inactive'">
                    {% raw %}{{ slot.is_active ? 'Aktiv' : 'Inaktiv' }}{% endraw %}
                  </span>
                </div>
                <div v-if="requiresCommunicationBinding(slot)" class="slot-row__binding">
                  <label class="slot-row__binding-label" :for="bindingInputId(slot)">Kommunikationsmodul</label>
                  <select
                    :id="bindingInputId(slot)"
                    v-model="bindingDrafts[slot.comp_id]"
                    class="slot-row__binding-select"
                    :disabled="savingBindings.has(slot.comp_id)"
                  >
                    <option value="">Nicht zugeordnet</option>
                    <option
                      v-for="option in availableModules"
                      :key="option.value"
                      :value="option.value"
                    >
                      {% raw %}{{ option.label }}{% endraw %}
                    </option>
                  </select>
                  <Button
                    label="Speichern"
                    size="small"
                    severity="secondary"
                    :disabled="!hasBindingChanged(slot)"
                    :loading="savingBindings.has(slot.comp_id)"
                    @click="saveBinding(slot)"
                  />
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * PluginsPage — Settings page for runtime plugin visibility and per-component
 * communication-module bindings.
 */
import axios from 'axios'
import { computed, onMounted, ref, watch } from 'vue'
import { pluginApi, type ResolvePluginsResponse, type UiManifestEntry } from '../../plugins/plugin.api'
import apiClient from '../../../api/http'
import Button from 'primevue/button'
import Tag from 'primevue/tag'

interface ModuleInstance {
  module_name: string
  instance_id: string
  alias?: string | null
}

interface ModuleOption {
  value: string
  label: string
}

interface PluginGroup {
  pluginId: string
  title: string
  icon: string | null
  version: string
  scopeType: string
  scopeTarget: string | null
  hasFrontendScope: boolean
  missingBindings: number
  slots: UiManifestEntry[]
}

const MODULE_NAME = (apiClient.defaults.baseURL ?? '').replace(/\/api$/, '').replace(/^\//, '')
const IS_MODULE_MANAGER = MODULE_NAME === 'v2_modulemanager'

const mmApi = axios.create({
  baseURL: '/v2_modulemanager/api',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  },
})

const refreshing = ref(true)
const cleanupInFlight = ref(false)
const resolveError = ref<string | null>(null)
const availableModules = ref<ModuleOption[]>([])
const currentInstanceTargets = ref(new Set<string>())
const resolvedEntriesBySlot = ref<Record<string, UiManifestEntry[]>>({})
const savingBindings = ref(new Set<string>())
const bindingDrafts = ref<Record<string, string>>({})

const isLoading = computed(() => refreshing.value && allEntries.value.length === 0)

function semverCompare(a: string, b: string): number {
  const parse = (value: string) => value.replace(/[^0-9.]/g, '').split('.').map(Number)
  const left = parse(a)
  const right = parse(b)
  for (let index = 0; index < Math.max(left.length, right.length); index += 1) {
    const diff = (left[index] ?? 0) - (right[index] ?? 0)
    if (diff !== 0) return diff
  }
  return 0
}

function normalizeModuleTarget(value: string | null | undefined): string {
  return (value ?? '').trim().replace(/_[0-9a-f]{32}$/i, '')
}

function canonicalModuleName(value: string | null | undefined): string {
  return normalizeModuleTarget(value).replace(/^v2_/, '')
}

function normalizeInstanceTarget(value: string | null | undefined): string {
  return (value ?? '').trim()
}

function uniqueSlotLabels(slot: UiManifestEntry): string[] {
  const labels = slot.slot_ids?.length ? slot.slot_ids : [slot.slot_id]
  return Array.from(new Set(labels.filter((sid): sid is string => Boolean(sid))))
}

function slotRowKey(slot: UiManifestEntry): string {
  return slot.comp_id || `${slot.assignment_id}-${slot.slot_id}`
}

function bindingInputId(slot: UiManifestEntry): string {
  return `binding-${slotRowKey(slot)}`
}

function hasFrontendScope(entry: UiManifestEntry): boolean {
  return Boolean(entry.is_frontend_scope || entry.slot_scope_type)
}

function isModuleAssignedSlot(entry: UiManifestEntry): boolean {
  if (entry.scope_type === 'GLOBAL' || entry.scope_type === 'BLUEPRINT') return true
  if (!entry.scope_target) return true
  if (entry.scope_type === 'MODULE') {
    return canonicalModuleName(entry.scope_target) === canonicalModuleName(MODULE_NAME)
  }
  if (entry.scope_type === 'INSTANCE') {
    const scopeTarget = normalizeInstanceTarget(entry.scope_target)
    if (!scopeTarget) return true
    if (canonicalModuleName(scopeTarget) === canonicalModuleName(MODULE_NAME)) return true
    return currentInstanceTargets.value.has(scopeTarget)
  }
  return false
}

function requiresCommunicationBinding(entry: UiManifestEntry): boolean {
  // GLOBAL frontend-only plugins (e.g. sdp-system-info) have no WASM host module.
  if (entry.scope_type === 'GLOBAL') return false
  return ['MODULE', 'BLUEPRINT', 'INSTANCE'].includes(entry.scope_type)
}

function hasBindingChanged(entry: UiManifestEntry): boolean {
  if (!entry.comp_id) return false
  return (bindingDrafts.value[entry.comp_id] ?? '') !== (entry.communication_module_name ?? '')
}

const allEntries = computed<UiManifestEntry[]>(() => {
  const byLogicalSlot = new Map<string, UiManifestEntry>()

  for (const entries of Object.values(resolvedEntriesBySlot.value)) {
    for (const entry of entries) {
      const scopeTarget = entry.scope_target ?? ''
      const logicalKey = `${entry.plugin_id}|${entry.scope_type}|${scopeTarget}|${entry.slot_id}`
      const existing = byLogicalSlot.get(logicalKey)
      if (!existing) {
        byLogicalSlot.set(logicalKey, {
          ...entry,
          slot_ids: uniqueSlotLabels(entry),
        })
        continue
      }
      if (semverCompare(entry.version, existing.version) <= 0) {
        continue
      }
      byLogicalSlot.set(logicalKey, {
        ...entry,
        slot_ids: Array.from(new Set([...uniqueSlotLabels(existing), ...uniqueSlotLabels(entry)])),
      })
    }
  }

  return Array.from(byLogicalSlot.values())
})

const groupedPlugins = computed<PluginGroup[]>(() => {
  const map = new Map<string, PluginGroup>()
  for (const entry of allEntries.value) {
    if (!map.has(entry.plugin_id)) {
      map.set(entry.plugin_id, {
        pluginId: entry.plugin_id,
        title: entry.title || entry.plugin_id,
        icon: entry.icon,
        version: entry.version,
        scopeType: entry.scope_type,
        scopeTarget: entry.scope_target,
        hasFrontendScope: hasFrontendScope(entry),
        missingBindings: 0,
        slots: [],
      })
    }
    const group = map.get(entry.plugin_id)!
    if (semverCompare(entry.version, group.version) > 0) {
      group.version = entry.version
      group.scopeType = entry.scope_type
      group.scopeTarget = entry.scope_target
      if (entry.icon) group.icon = entry.icon
      if (entry.title) group.title = entry.title
    }
    if (hasFrontendScope(entry)) {
      group.hasFrontendScope = true
    }
    if (requiresCommunicationBinding(entry) && !entry.communication_module_name) {
      group.missingBindings += 1
    }
    group.slots.push(entry)
  }
  return [...map.values()].sort((left, right) => left.title.localeCompare(right.title))
})

const pluginSections = computed(() => {
  const moduleAssigned = groupedPlugins.value.filter((plugin) => plugin.slots.some(isModuleAssignedSlot))
  const uiOnly = groupedPlugins.value.filter((plugin) => plugin.slots.every((slot) => !isModuleAssignedSlot(slot)))

  return [
    {
      id: 'module-assigned',
      title: 'Modulzugeordnete Plugins',
      subtitle: 'Plugins, deren Laufzeit-Scope dieses Modul selbst einschließt.',
      groups: moduleAssigned,
    },
    {
      id: 'ui-only',
      title: 'Nur UI-zugeordnete Komponenten',
      subtitle: 'UI-Komponenten, die hier gerendert werden, deren Plugin aber aus einem anderen Modul-Scope stammt.',
      groups: uiOnly,
    },
  ].filter((section) => section.groups.length > 0)
})

watch(allEntries, (entries) => {
  const next: Record<string, string> = {}
  for (const entry of entries) {
    next[entry.comp_id] = bindingDrafts.value[entry.comp_id] ?? entry.communication_module_name ?? ''
  }
  bindingDrafts.value = next
}, { immediate: true })

function scopeLabel(scopeType: string, scopeTarget: string | null): string {
  if (scopeType === 'GLOBAL') return 'Global — in allen Modulen aktiv'
  if (scopeType === 'BLUEPRINT') return `Blueprint: ${scopeTarget ?? '–'}`
  if (scopeType === 'MODULE') return `Modul: ${scopeTarget ?? '–'}`
  if (scopeType === 'INSTANCE') return `Instanz: ${scopeTarget ?? '–'}`
  return scopeType
}

async function loadAvailableModules(): Promise<void> {
  const response = await mmApi.get('/modules/instances', { params: { include_hidden: true } })
  const groupedModules = (response.data?.modules ?? {}) as Record<string, ModuleInstance[]>
  const moduleOptions: ModuleOption[] = []
  const ownTargets = new Set<string>()

  for (const [groupName, items] of Object.entries(groupedModules)) {
    const moduleName = String(groupName ?? '').trim()
    if (!moduleName) continue

    for (const item of items ?? []) {
      const instanceId = String((item as any).instance_id ?? (item as any).module_id ?? '').trim()
      if (!instanceId) continue

      const fullTarget = `${moduleName}_${instanceId}`
      const aliasText = item.alias ? ` (${item.alias})` : ''
      moduleOptions.push({
        value: fullTarget,
        label: `${moduleName} · ${instanceId}${aliasText}`,
      })

      if (canonicalModuleName(moduleName) === canonicalModuleName(MODULE_NAME)) {
        ownTargets.add(fullTarget)
        ownTargets.add(moduleName)
        ownTargets.add(`${canonicalModuleName(moduleName)}_${instanceId}`)
        ownTargets.add(canonicalModuleName(moduleName))
      }
    }
  }

  moduleOptions.sort((left, right) => left.label.localeCompare(right.label))
  availableModules.value = moduleOptions
  currentInstanceTargets.value = ownTargets
}

function mergeResolvedEntries(
  target: Record<string, UiManifestEntry[]>,
  response: ResolvePluginsResponse
): void {
  const uniqueBySlot = new Map<string, Set<string>>()

  for (const [slotId, entries] of Object.entries(target)) {
    uniqueBySlot.set(
      slotId,
      new Set(entries.map((entry) => `${entry.comp_id}|${entry.assignment_id}|${entry.scope_type}|${entry.scope_target ?? ''}`))
    )
  }

  for (const [slotId, entries] of Object.entries(response.ui_slots ?? {})) {
    if (!target[slotId]) target[slotId] = []
    const unique = uniqueBySlot.get(slotId) ?? new Set<string>()
    for (const entry of entries) {
      const key = `${entry.comp_id}|${entry.assignment_id}|${entry.scope_type}|${entry.scope_target ?? ''}`
      if (unique.has(key)) continue
      unique.add(key)
      target[slotId].push(entry)
    }
    uniqueBySlot.set(slotId, unique)
  }
}

async function resolveEntriesForSettings(): Promise<void> {
  const merged: Record<string, UiManifestEntry[]> = {}
  const requestMap = new Map<string, Promise<ResolvePluginsResponse>>()
  const moduleNameVariants = Array.from(new Set([MODULE_NAME, canonicalModuleName(MODULE_NAME)]).values()).filter(Boolean)

  for (const moduleVariant of moduleNameVariants) {
    const key = `MODULE|${moduleVariant}|${moduleVariant}`
    requestMap.set(
      key,
      pluginApi.resolvePlugins({
        scope_type: 'MODULE',
        scope_target: moduleVariant,
        module_name: moduleVariant,
        request_source: 'frontend',
      })
    )
  }

  const instanceTargets = [...currentInstanceTargets.value].filter((target) => target.includes('_'))
  for (const target of instanceTargets) {
    for (const moduleVariant of moduleNameVariants) {
      const key = `INSTANCE|${target}|${moduleVariant}`
      requestMap.set(
        key,
        pluginApi.resolvePlugins({
          scope_type: 'INSTANCE',
          scope_target: target,
          module_name: moduleVariant,
          request_source: 'frontend',
        })
      )
    }
  }

  const responses = await Promise.all(requestMap.values())
  for (const response of responses) {
    mergeResolvedEntries(merged, response)
  }
  resolvedEntriesBySlot.value = merged
}

async function runCleanupAssignmentsInBackground(): Promise<void> {
  if (!IS_MODULE_MANAGER || cleanupInFlight.value) return
  cleanupInFlight.value = true
  try {
    await mmApi.post('/plugin_admin_service/cleanup/assignments', {
      dry_run: false,
      purge_inactive: false,
      dedupe_components: true,
    })
  } catch (error) {
    console.warn('[PluginsPage] cleanup assignments failed:', error)
  } finally {
    cleanupInFlight.value = false
  }
}

async function doRefresh(): Promise<void> {
  refreshing.value = true
  resolveError.value = null
  void runCleanupAssignmentsInBackground()
  try {
    await loadAvailableModules()
    await resolveEntriesForSettings()
  } catch (error: any) {
    resolveError.value = error?.message ?? 'Plugin-Daten konnten nicht geladen werden.'
    resolvedEntriesBySlot.value = {}
  } finally {
    refreshing.value = false
  }
}

async function saveBinding(entry: UiManifestEntry): Promise<void> {
  if (!entry.comp_id) {
    resolveError.value = 'UI-Komponenten-ID fehlt. Plugin-Daten bitte neu laden.'
    return
  }

  const nextValue = (bindingDrafts.value[entry.comp_id] ?? '').trim()
  const pending = new Set(savingBindings.value)
  pending.add(entry.comp_id)
  savingBindings.value = pending
  try {
    await mmApi.patch(`/plugin_admin_service/ui-component-bindings/${encodeURIComponent(entry.comp_id)}`, {
      communication_module_name: nextValue || null,
    })
    await doRefresh()
  } catch (error: any) {
    const status = Number(error?.response?.status ?? 0)
    const isNotFound = status === 404

    // Handle stale comp_id references by refreshing and retrying with the newest resolve result.
    if (isNotFound) {
      await doRefresh()

      const refreshedEntry = allEntries.value.find((candidate) => {
        return (
          candidate.assignment_id === entry.assignment_id
          && candidate.plugin_id === entry.plugin_id
          && candidate.slot_id === entry.slot_id
          && candidate.scope_type === entry.scope_type
          && (candidate.scope_target ?? '') === (entry.scope_target ?? '')
        )
      })

      if (refreshedEntry && refreshedEntry.comp_id !== entry.comp_id) {
        await mmApi.patch(`/plugin_admin_service/ui-component-bindings/${encodeURIComponent(refreshedEntry.comp_id)}`, {
          communication_module_name: nextValue || null,
        })
        await doRefresh()
        return
      }
    }

    resolveError.value = error?.response?.data?.detail ?? error?.message ?? 'Kommunikationsmodul konnte nicht gespeichert werden.'
  } finally {
    const updated = new Set(savingBindings.value)
    updated.delete(entry.comp_id)
    savingBindings.value = updated
  }
}

onMounted(() => {
  void doRefresh()
})
</script>

<style scoped>
.plugins-settings {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.plugins-settings__header,
.plugins-section__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}

.plugins-settings__title,
.plugins-section__title {
  font-size: 1.125rem;
  font-weight: 600;
  color: var(--text-color);
  margin: 0 0 0.25rem 0;
}

.plugins-settings__subtitle,
.plugins-section__subtitle {
  font-size: 0.875rem;
  color: var(--text-color-secondary);
  margin: 0;
}

.plugins-settings__empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 3rem 2rem;
  gap: 0.75rem;
  text-align: center;
  color: var(--text-color-secondary);
}

.plugins-settings__empty-hint {
  font-size: 0.875rem;
  opacity: 0.75;
}

.plugins-settings__list,
.plugins-section,
.plugins-section__list,
.plugin-row__slots {
  display: flex;
  flex-direction: column;
}

.plugins-settings__list,
.plugins-section,
.plugins-section__list {
  gap: 0.75rem;
}

.plugins-section {
  gap: 0.75rem;
}

.plugin-row {
  background: var(--surface-card);
  border: 1px solid var(--surface-border);
  border-radius: 8px;
  padding: 1rem;
}

.plugin-row__header {
  display: flex;
  align-items: flex-start;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}

.plugin-row__icon {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--surface-section, var(--surface-hover));
  border-radius: 8px;
  flex-shrink: 0;
}

.plugin-row__meta {
  min-width: 0;
  flex: 1;
}

.plugin-row__name {
  font-weight: 600;
  font-size: 0.95rem;
  color: var(--text-color);
  margin-bottom: 0.25rem;
}

.plugin-row__id {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  flex-wrap: wrap;
}

.plugin-row__id code {
  font-size: 0.75rem;
  color: var(--text-color-secondary);
  background: var(--surface-hover);
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
}

.plugin-row__slots {
  border-top: 1px solid var(--surface-border);
  padding-top: 0.75rem;
  gap: 0.75rem;
}

.slot-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 0.75rem;
  padding: 0.65rem 0.75rem;
  border-radius: 6px;
  background: var(--surface-section, var(--surface-ground));
}

.slot-row__info {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  min-width: 0;
  flex-wrap: wrap;
}

.slot-row__icon {
  color: var(--text-color-secondary);
  font-size: 0.8rem;
}

.slot-row__ids {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  align-items: center;
}

.slot-row__label {
  font-size: 0.8rem;
  font-family: monospace;
  color: var(--text-color);
}

.slot-row__actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  justify-content: flex-end;
}

.slot-row__binding {
  grid-column: 1 / -1;
  display: grid;
  grid-template-columns: minmax(180px, 220px) minmax(0, 1fr) auto;
  gap: 0.75rem;
  align-items: center;
}

.slot-row__binding-label {
  font-size: 0.8rem;
  color: var(--text-color-secondary);
}

.slot-row__binding-select {
  width: 100%;
  min-height: 2.25rem;
  border: 1px solid var(--surface-border);
  border-radius: 6px;
  background: var(--surface-card);
  color: var(--text-color);
  padding: 0 0.75rem;
}

.slot-row__status {
  font-size: 0.75rem;
  font-weight: 500;
}

.slot-row__status--active {
  color: var(--green-500, #22c55e);
}

.slot-row__status--inactive {
  color: var(--orange-500, #f59e0b);
}

@media (max-width: 900px) {
  .slot-row {
    grid-template-columns: 1fr;
  }

  .slot-row__actions {
    justify-content: flex-start;
  }

  .slot-row__binding {
    grid-template-columns: 1fr;
  }
}

.slot-row__status--active {
  color: var(--p-green-500, #22c55e);
}

.slot-row__status--inactive {
  color: var(--text-color-secondary);
}
</style>
