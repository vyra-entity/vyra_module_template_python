/**
 * Composable for resolving a plugin's fallback icon and background color
 * based on the plugin name ID when no custom icon asset is available.
 *
 * Uses pattern matching to assign semantically appropriate PrimeIcons
 * and matching tinted backgrounds so the grid cards look distinct at a glance.
 */

export interface PluginIconInfo {
  /** PrimeIcon CSS class, e.g. "pi pi-users" */
  iconClass: string
  /** Tailwind / CSS color token for the icon foreground */
  color: string
  /** Semi-transparent background color for the icon container */
  background: string
}

const RULES: Array<{ pattern: RegExp; icon: string; color: string; bg: string }> = [
  { pattern: /user|auth|identity|account|login|permission/,    icon: 'pi pi-users',          color: '#6366f1', bg: 'rgba(99,102,241,0.12)' },
  // ── Project-specific plugin types (before generic "project" rule) ──────────
  { pattern: /project.?board|board.?plugin|\bkanban\b/,          icon: 'pi pi-table',         color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
  { pattern: /project.?builder|builder.?plugin|\bscaffold\b/,    icon: 'pi pi-hammer',        color: '#d97706', bg: 'rgba(217,119,6,0.12)' },
  { pattern: /project.?browser|browser.?plugin|\bexplorer\b/,    icon: 'pi pi-search',        color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  // ── Side-dock / system info panels ───────────────────────────────────────
  { pattern: /\bsdp\b|side.?dock/,                               icon: 'pi pi-align-left',    color: '#7c3aed', bg: 'rgba(124,58,237,0.12)' },
  { pattern: /system.?info|sysinfo|info.?panel/,                 icon: 'pi pi-info-circle',   color: '#0891b2', bg: 'rgba(8,145,178,0.12)' },
  { pattern: /dashboard|analytics|metric|stat|chart|bi/,        icon: 'pi pi-chart-bar',      color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
  { pattern: /monitor|observation|health|alive|watchdog/,        icon: 'pi pi-heart-fill',     color: '#ef4444', bg: 'rgba(239,68,68,0.12)' },
  { pattern: /project|task|ticket|workflow|kanban|sprint/,        icon: 'pi pi-folder-open',   color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
  { pattern: /robot|arm|actuator|motor|servo|cnc|plc/,            icon: 'pi pi-wrench',        color: '#8b5cf6', bg: 'rgba(139,92,246,0.12)' },
  { pattern: /camera|vision|image|video|capture|scan/,            icon: 'pi pi-camera',        color: '#0ea5e9', bg: 'rgba(14,165,233,0.12)' },
  { pattern: /database|db|storage|data|warehouse|pool/,           icon: 'pi pi-database',      color: '#64748b', bg: 'rgba(100,116,139,0.12)' },
  { pattern: /network|connect|socket|mqtt|zenoh|websocket|grpc/,  icon: 'pi pi-sitemap',       color: '#06b6d4', bg: 'rgba(6,182,212,0.12)' },
  { pattern: /security|cert|ssl|tls|key|vault|crypto/,            icon: 'pi pi-shield',        color: '#eab308', bg: 'rgba(234,179,8,0.12)' },
  { pattern: /setting|config|pref|option|param/,                  icon: 'pi pi-sliders-h',     color: '#6b7280', bg: 'rgba(107,114,128,0.12)' },
  { pattern: /log|trace|audit|debug|journal/,                     icon: 'pi pi-list',          color: '#78716c', bg: 'rgba(120,113,108,0.12)' },
  { pattern: /notify|alert|notification|alarm|warning/,           icon: 'pi pi-bell',          color: '#f59e0b', bg: 'rgba(245,158,11,0.12)' },
  { pattern: /io|signal|sensor|gpio|plc|fieldbus|opc/,            icon: 'pi pi-wave-pulse',    color: '#22c55e', bg: 'rgba(34,197,94,0.12)' },
  { pattern: /report|export|pdf|csv|excel|print/,                 icon: 'pi pi-file',          color: '#84cc16', bg: 'rgba(132,204,22,0.12)' },
  { pattern: /map|nav|gps|location|geograph/,                     icon: 'pi pi-map',           color: '#14b8a6', bg: 'rgba(20,184,166,0.12)' },
  { pattern: /calendar|schedule|time|event|plan/,                 icon: 'pi pi-calendar',      color: '#a78bfa', bg: 'rgba(167,139,250,0.12)' },
  { pattern: /email|mail|message|chat|slack|teams/,               icon: 'pi pi-envelope',      color: '#60a5fa', bg: 'rgba(96,165,250,0.12)' },
  { pattern: /tool|util|helper|dev|debug/,                        icon: 'pi pi-wrench',        color: '#9ca3af', bg: 'rgba(156,163,175,0.12)' },
  { pattern: /module|container|service|micro/,                    icon: 'pi pi-box',           color: '#2196f3', bg: 'rgba(33,150,243,0.12)' },
]

/** Default fallback icon when no rule matches */
const DEFAULT: PluginIconInfo = {
  iconClass:  'pi pi-puzzle',
  color:      'var(--p-primary-color, #2196f3)',
  background: 'var(--p-surface-ground, #f9fafb)',
}

/**
 * Returns icon class, foreground color, and background color for a plugin.
 *
 * @param pluginNameId - The unique plugin name ID (e.g. "v2_usermanager")
 */
export function usePluginIcon(pluginNameId: string): PluginIconInfo {
  const name = pluginNameId.toLowerCase()
  for (const rule of RULES) {
    if (rule.pattern.test(name)) {
      return { iconClass: rule.icon, color: rule.color, background: rule.bg }
    }
  }
  return { ...DEFAULT }
}

/** True when the manifest icon value is a PrimeIcons CSS class. */
export function isPrimeIcon(icon: string): boolean {
  const trimmed = icon.trim()
  return trimmed.startsWith('pi ') || trimmed.startsWith('pi-')
}

/** Resolve a plugin icon asset path to the module-manager asset URL. */
export function resolvePluginIconUrl(
  icon: string | null | undefined,
  pluginId: string,
  version: string,
): string | null {
  if (!icon || isPrimeIcon(icon)) return null

  const trimmed = icon.trim()
  if (/^https?:\/\//.test(trimmed) || trimmed.startsWith('/')) {
    return trimmed
  }

  const normalizedPath = trimmed
    .replace(/^\/+/, '')
    .replace(new RegExp(`^plugins/${pluginId}/${version}/`), '')

  return `/v2_modulemanager/api/plugin/assets/${pluginId}/${version}/${normalizedPath}`
}
