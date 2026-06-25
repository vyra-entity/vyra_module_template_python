/**
 * Sidebar Pinia Store
 *
 * Manages collapse state (persisted to localStorage) and the registry of
 * navigation items.  Static items for the 4 built-in routes are registered
 * here; plugins can call registerItem() to add their own entries at runtime.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { SidebarNavItem, SidebarNavGroup, SidebarGroup, SettingsNavItem } from '../types/sidebar'

const STORAGE_KEY = 'vyra:sidebar:collapsed'

/** Visual order and labels for the available groups */
const GROUP_CONFIG: { id: SidebarGroup; label: string; order: number }[] = [
  { id: 'main',     label: 'Navigation',   order: 0 },
  { id: 'analysis', label: 'Analyse',      order: 1 },
  { id: 'system',   label: 'System',       order: 2 },
  { id: 'settings', label: 'Einstellungen', order: 3 },
]

/** Built-in navigation items for {{ module_name }} */
const DEFAULT_ITEMS: SidebarNavItem[] = [
  {
    id:        'home',
    label:     'Home',
    icon:      'pi pi-home',
    routeName: 'home',
    group:     'main',
    priority:  100,
  },
  {
    id:        'settings',
    label:     'Einstellungen',
    icon:      'pi pi-cog',
    routeName: 'settings-general',
    group:     'settings',
    priority:  50,
  },
]

/** Default settings sub-navigation items for {{ module_name }} */
const DEFAULT_SETTINGS_ITEMS: SettingsNavItem[] = [
  { id: 'settings-general',       label: 'Allgemein',          icon: 'pi pi-sliders-h', routeName: 'settings-general',       priority: 100 },
  { id: 'settings-appearance',    label: 'Darstellung',        icon: 'pi pi-palette',   routeName: 'settings-appearance',    priority: 90  },
  { id: 'settings-notifications', label: 'Benachrichtigungen', icon: 'pi pi-bell',      routeName: 'settings-notifications', priority: 80  },
  { id: 'settings-auth',          label: 'Authentifizierung',  icon: 'pi pi-lock',      routeName: 'settings-auth',          priority: 70  },
  { id: 'settings-plugins',        label: 'Plugins',            icon: 'pi pi-box',       routeName: 'settings-plugins',        priority: 60  },
  { id: 'settings-about',          label: 'About',              icon: 'pi pi-info-circle', routeName: 'settings-about',          priority: 10  },
]

export const useSidebarStore = defineStore('sidebar', () => {
  // ─── Collapse state (persisted) ────────────────────────────────────────────
  const isCollapsed = ref<boolean>(
    localStorage.getItem(STORAGE_KEY) === 'true'
  )

  function toggleCollapse(): void {
    isCollapsed.value = !isCollapsed.value
    localStorage.setItem(STORAGE_KEY, String(isCollapsed.value))
  }

  function setCollapsed(value: boolean): void {
    isCollapsed.value = value
    localStorage.setItem(STORAGE_KEY, String(value))
  }

  // ─── Item registry ──────────────────────────────────────────────────────────
  const navItems = ref<SidebarNavItem[]>([...DEFAULT_ITEMS])

  function registerItem(item: SidebarNavItem): void {
    const existing = navItems.value.findIndex(i => i.id === item.id)
    if (existing !== -1) {
      navItems.value[existing] = item   // update if already registered
    } else {
      navItems.value.push(item)
    }
  }

  function unregisterItem(id: string): void {
    const idx = navItems.value.findIndex(i => i.id === id)
    if (idx !== -1) navItems.value.splice(idx, 1)
  }

  function updateBadge(id: string, count: number): void {
    const item = navItems.value.find(i => i.id === id)
    if (item) item.badge = count > 0 ? count : undefined
  }

  // ─── Computed: items grouped and sorted ────────────────────────────────────
  const groupedItems = computed<SidebarNavGroup[]>(() => {
    const result: SidebarNavGroup[] = []

    for (const group of GROUP_CONFIG) {
      const items = navItems.value
        .filter(i => i.group === group.id)
        .sort((a, b) => b.priority - a.priority)

      if (items.length > 0) {
        result.push({ id: group.id, label: group.label, items })
      }
    }
    return result
  })

  // ─── Settings item registry ─────────────────────────────────────────────────────────
  const _settingsItems = ref<SettingsNavItem[]>([...DEFAULT_SETTINGS_ITEMS])

  /** Sorted settings items – higher priority first */
  const settingsItems = computed<SettingsNavItem[]>(() =>
    [..._settingsItems.value].sort((a, b) => b.priority - a.priority)
  )

  function registerSettingsItem(item: SettingsNavItem): void {
    const idx = _settingsItems.value.findIndex(i => i.id === item.id)
    if (idx !== -1) {
      _settingsItems.value[idx] = item
    } else {
      _settingsItems.value.push(item)
    }
  }

  function unregisterSettingsItem(id: string): void {
    const idx = _settingsItems.value.findIndex(i => i.id === id)
    if (idx !== -1) _settingsItems.value.splice(idx, 1)
  }

  return {
    // state
    isCollapsed,
    navItems,
    // getters
    groupedItems,
    settingsItems,
    // actions
    toggleCollapse,
    setCollapsed,
    registerItem,
    unregisterItem,
    updateBadge,
    registerSettingsItem,
    unregisterSettingsItem,
  }
})
