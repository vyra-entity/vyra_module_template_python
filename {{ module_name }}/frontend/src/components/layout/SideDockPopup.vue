<template>
  <!-- Side-Dock-Popup (SDP): floating icon tabs pinned to the right edge.
       Each registered pocket becomes an independent floating widget:
       - Icon tab always visible on the right edge.
       - Hover: label slides out to the left.
       - Click: popup panel opens to the left of the strip.
       - Pinned: popup stays open; main content remains fully accessible.
       - Not pinned + click outside: backdrop catches clicks → close all unpinned. -->
  <Teleport to="body">
    <!-- Global backdrop — semi-transparent, only when an unpinned pocket is open -->
    <Transition name="sdp-fade">
      <div
        v-if="hasUnpinnedOpen"
        class="sdp-backdrop"
        @click="sdpStore.closeAllUnpinned()"
        aria-hidden="true"
      />
    </Transition>

    <!-- Right-edge strip: one widget per visible pocket (tabs only, no popups) -->
    <div
      class="sdp-strip"
      :class="{ 'sdp-strip--dragging': isStripDragging }"
      :style="stripStyle"
      role="complementary"
      aria-label="Side Dock Widgets"
    >
      <div
        v-for="pocket in visiblePockets"
        :key="pocket.id"
        class="sdp-widget"
        :data-pocket-id="pocket.id"
        :class="{
          'sdp-widget--open': pocket.isOpen,
          'sdp-widget--pinned': pocket.isPinned,
        }"
      >
        <!-- Always-visible tab (icon + slide-out label) -->
        <button
          class="sdp-tab"
          :style="tabStyle(pocket)"
          @mousedown.prevent="startWidgetDrag($event, pocket)"
          @click="onTabClick($event, pocket)"
          :aria-label="pocket.title"
          :title="pocket.title"
          :aria-expanded="pocket.isOpen"
        >
          <i :class="pocket.icon" class="sdp-tab-icon" aria-hidden="true" />
          <span class="sdp-tab-label">{% raw %}{{ pocket.title }}{% endraw %}</span>
        </button>
      </div>
    </div>

    <!-- Popups rendered OUTSIDE the strip so they are not affected by the strip's
         CSS transform. Each popup uses position:fixed with absolute viewport
         coordinates, completely decoupled from widget movement. -->
    <template v-for="pocket in visiblePockets" :key="pocket.id + '-popup'">
      <Transition name="sdp-popup">
        <div
          v-if="pocket.isOpen"
          class="sdp-popup"
          :class="{ 'sdp-popup--dragging': isDragging(pocket.id) }"
          :style="popupStyle(pocket.id)"
          role="dialog"
          :aria-label="pocket.title"
          @click.stop
        >
          <div
            class="sdp-popup-header"
            @mousedown.prevent="startDrag($event, pocket.id)"
          >
            <i :class="pocket.icon" class="sdp-popup-header-icon" aria-hidden="true" />
            <span class="sdp-popup-title">{% raw %}{{ pocket.title }}{% endraw %}</span>
            <div class="sdp-popup-header-actions">
              <button
                v-if="pocket.isPinnable"
                class="sdp-icon-btn"
                :class="{ 'sdp-icon-btn--active': pocket.isPinned }"
                @click.stop="pocket.isPinned ? sdpStore.unpinPocket(pocket.id) : sdpStore.pinPocket(pocket.id)"
                :title="pocket.isPinned ? 'Floating (unpin)' : 'Pinned (pin)'"
                :aria-label="pocket.isPinned ? 'Unpin panel' : 'Pin panel open'"
              >
                <i :class="pocket.isPinned ? 'pi pi-lock' : 'pi pi-lock-open'" aria-hidden="true" />
              </button>
              <button
                class="sdp-icon-btn"
                @click.stop="closePocket(pocket)"
                title="Schließen"
                aria-label="Panel schließen"
              >
                <i class="pi pi-times" aria-hidden="true" />
              </button>
            </div>
          </div>
          <div class="sdp-popup-body">
            <component
              :is="pocket.component"
              :pluginApi="pluginApi"
              :sdpApi="sdpStore.getPocketApi(pocket.id)"
            />
          </div>
          <div class="sdp-popup-footer" />
        </div>
      </Transition>
    </template>

    <!-- Floating drop proxy shown while dragging a widget left for detached popup placement. -->
    <div
      v-if="dropProxy.active"
      class="sdp-drop-proxy"
      :style="dropProxyStyle"
      aria-hidden="true"
    >
      <i :class="dropProxy.icon" class="sdp-drop-proxy-icon" aria-hidden="true" />
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useSideDockPopupStore } from '../../store/sideDockPopup'
import type { SdpPocket } from '../../store/sideDockPopup'
import { PLUGIN_API_INJECTION_KEY } from '../../composables/usePluginApi'

const sdpStore = useSideDockPopupStore()
const route = useRoute()
const pluginApi = inject(PLUGIN_API_INJECTION_KEY, undefined)
const activeContext = computed(() => String(route.name ?? route.path))
const POPUP_POS_STORAGE_KEY = 'sdp-popup-positions-v2'
const POPUP_WIDTH_PX = 300
const POPUP_RIGHT_PX = 130
const DROP_PROXY_SIZE_PX = 44
const DROP_PROXY_MARGIN_PX = 8
const DROP_TRIGGER_LEFT_PX = 56

/** Inline style for the strip — positions it at 25% from top + persistent Y offset. */
const stripStyle = computed(() => ({
  top: `calc(25% + ${sdpStore.stripYOffset}px)`,
}))

/** All pockets that pass the current context scope filter, sorted by priority. */
const visiblePockets = computed((): SdpPocket[] =>
  sdpStore.sortedPockets.filter(
    (p) =>
      p.contextScope.length === 0 ||
      p.contextScope.some((s) => activeContext.value.includes(s)),
  ),
)

/** True when at least one unpinned pocket is open → show the backdrop. */
const hasUnpinnedOpen = computed(() =>
  sdpStore.pockets.some((p) => p.isOpen && !p.isPinned),
)

// ── Drag state ──────────────────────────────────────────────────────────────
/**
 * Per-pocket popup position as absolute viewport coordinates (px).
 * Decoupled from the widget position — popups do NOT move when the strip moves.
 */
const popupPos = ref<Record<string, { x: number; y: number }>>(loadPopupPositions())

/** Restore persisted popup positions from localStorage. */
function loadPopupPositions(): Record<string, { x: number; y: number }> {
  try {
    const raw = localStorage.getItem(POPUP_POS_STORAGE_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return {}

    const out: Record<string, { x: number; y: number }> = {}
    for (const [id, val] of Object.entries(parsed as Record<string, unknown>)) {
      if (!val || typeof val !== 'object') continue
      const x = Number((val as { x?: unknown }).x)
      const y = Number((val as { y?: unknown }).y)
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue
      // Discard positions that are completely off-screen
      if (x < -POPUP_WIDTH_PX || x > window.innerWidth) continue
      if (y < -60 || y > window.innerHeight) continue
      out[id] = { x, y }
    }
    return out
  } catch {
    return {}
  }
}

watch(
  popupPos,
  (positions) => {
    localStorage.setItem(POPUP_POS_STORAGE_KEY, JSON.stringify(positions))
  },
  { deep: true },
)

/**
 * Compute the initial popup viewport position anchored to the widget tab.
 * Called the first time a pocket opens (or when no saved position exists).
 */
function initPopupPosition(id: string): void {
  const widgetEl = getWidgetElement(id)
  if (!widgetEl) return
  const rect = widgetEl.getBoundingClientRect()
  popupPos.value[id] = {
    x: rect.right - POPUP_RIGHT_PX - POPUP_WIDTH_PX,
    y: clamp(rect.top, 12, window.innerHeight - 120),
  }
}

/** Auto-initialize position for newly opened pockets that have no saved position. */
watch(
  () => sdpStore.pockets.filter((p) => p.isOpen).map((p) => p.id),
  (openIds) => {
    for (const id of openIds) {
      if (!popupPos.value[id]) {
        nextTick(() => initPopupPosition(id))
      }
    }
  },
)

/** ID of the pocket currently being dragged, or null. */
const draggingId = ref<string | null>(null)

/** True while dragging the SDP widget strip (tab drag). */
const isStripDragging = ref(false)

/** Pocket ID for which the next click should be ignored after dragging. */
const suppressClickPocketId = ref<string | null>(null)

/** Temporary square proxy used as detached drop target during left drag. */
const dropProxy = ref<{ active: boolean; pocketId: string | null; x: number; y: number; icon: string }>({
  active: false,
  pocketId: null,
  x: 0,
  y: 0,
  icon: 'pi pi-th-large',
})

/** Inline style for the detached drop proxy. */
const dropProxyStyle = computed(() => ({
  left: `${dropProxy.value.x}px`,
  top: `${dropProxy.value.y}px`,
}))

/** Whether a given pocket popup is being dragged right now. */
function isDragging(id: string): boolean {
  return draggingId.value === id
}

/**
 * Inline style for the popup.
 * Uses absolute viewport coordinates (position: fixed) so the popup is fully
 * decoupled from the strip/widget position.
 */
function popupStyle(id: string): Record<string, string> {
  const pos = popupPos.value[id]
  if (!pos) return {}
  return { left: `${pos.x}px`, top: `${pos.y}px` }
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

/** Find the current widget element by pocket ID. */
function getWidgetElement(pocketId: string): HTMLElement | null {
  return Array.from(document.querySelectorAll<HTMLElement>('.sdp-widget')).find(
    (el) => el.dataset.pocketId === pocketId,
  ) ?? null
}

/** Position popup at the coordinates where the drop proxy was released. */
function placePopupAtDropPosition(pocketId: string, dropX: number, dropY: number): void {
  popupPos.value[pocketId] = {
    x: clamp(dropX, 12, window.innerWidth - POPUP_WIDTH_PX - 12),
    y: clamp(dropY, 12, window.innerHeight - 120),
  }
}

/** Update detached drop proxy coordinates around the cursor and keep it on-screen. */
function updateDropProxyPosition(clientX: number, clientY: number): void {
  dropProxy.value.x = clamp(
    clientX - DROP_PROXY_SIZE_PX / 2,
    DROP_PROXY_MARGIN_PX,
    window.innerWidth - DROP_PROXY_SIZE_PX - DROP_PROXY_MARGIN_PX,
  )
  dropProxy.value.y = clamp(
    clientY - DROP_PROXY_SIZE_PX / 2,
    DROP_PROXY_MARGIN_PX,
    window.innerHeight - DROP_PROXY_SIZE_PX - DROP_PROXY_MARGIN_PX,
  )
}

/**
 * Initiate a drag on mousedown inside the popup header.
 * Moves the popup by updating its absolute viewport position directly.
 */
function startDrag(e: MouseEvent, id: string): void {
  const startX = e.clientX
  const startY = e.clientY
  const initPos = { ...(popupPos.value[id] ?? { x: 0, y: 0 }) }

  draggingId.value = id

  const onMove = (ev: MouseEvent) => {
    popupPos.value[id] = {
      x: initPos.x + (ev.clientX - startX),
      y: initPos.y + (ev.clientY - startY),
    }
  }

  const onUp = () => {
    draggingId.value = null
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

/** Close a pocket while keeping its last drag offset for reload persistence. */
function closePocket(pocket: SdpPocket): void {
  sdpStore.closePocketForcefully(pocket.id)
}

/** Toggle a pocket open / closed on tab click. */
function togglePocket(pocket: SdpPocket): void {
  if (pocket.isOpen) {
    closePocket(pocket)
  } else {
    sdpStore.openPocket(pocket.id)
  }
}

/** Keep the complete strip inside the viewport while moving it vertically. */
function getStripOffsetBounds(): { minOffset: number; maxOffset: number } {
  const stripEl = document.querySelector('.sdp-strip') as HTMLElement | null
  const stripH = stripEl?.offsetHeight ?? 60
  const baseY = window.innerHeight * 0.25
  return {
    minOffset: stripH / 2 - baseY,
    maxOffset: window.innerHeight - stripH / 2 - baseY,
  }
}

/** Ignore the synthetic click that follows a drag gesture. */
function onTabClick(event: MouseEvent, pocket: SdpPocket): void {
  if (suppressClickPocketId.value === pocket.id) {
    event.preventDefault()
    event.stopPropagation()
    suppressClickPocketId.value = null
    return
  }
  togglePocket(pocket)
}

/**
 * Dragging a widget moves the complete strip on Y.
 * Dragging left spawns a detached drop proxy; releasing opens popup at that spot.
 * Popups are position:fixed and fully decoupled — they do NOT move when the strip moves.
 */
function startWidgetDrag(e: MouseEvent, pocket: SdpPocket): void {
  if (e.button !== 0) return

  const startX = e.clientX
  const startY = e.clientY
  const initOffset = sdpStore.stripYOffset
  let moved = false
  let detachedForDrop = false

  isStripDragging.value = true
  document.body.style.cursor = 'grabbing'

  const onMove = (ev: MouseEvent) => {
    const dx = ev.clientX - startX
    const dy = ev.clientY - startY

    if (!moved && Math.hypot(dx, dy) >= 4) {
      moved = true
    }

    if (moved && !detachedForDrop) {
      const { minOffset, maxOffset } = getStripOffsetBounds()
      const raw = initOffset + dy
      sdpStore.stripYOffset = Math.max(minOffset, Math.min(maxOffset, raw))
      // Popups are position:fixed — no compensation needed
    }

    if (!detachedForDrop && startX - ev.clientX >= DROP_TRIGGER_LEFT_PX) {
      detachedForDrop = true
      dropProxy.value.active = true
      dropProxy.value.pocketId = pocket.id
      dropProxy.value.icon = pocket.icon
      updateDropProxyPosition(ev.clientX, ev.clientY)
    }

    if (detachedForDrop) {
      updateDropProxyPosition(ev.clientX, ev.clientY)
    }
  }

  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)

    if (detachedForDrop && dropProxy.value.active && dropProxy.value.pocketId === pocket.id) {
      sdpStore.openPocket(pocket.id)
      placePopupAtDropPosition(pocket.id, dropProxy.value.x, dropProxy.value.y)
    }

    dropProxy.value.active = false
    dropProxy.value.pocketId = null

    isStripDragging.value = false
    document.body.style.cursor = ''

    if (moved || detachedForDrop) {
      suppressClickPocketId.value = pocket.id
      window.setTimeout(() => {
        if (suppressClickPocketId.value === pocket.id) {
          suppressClickPocketId.value = null
        }
      }, 0)
    }
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// ── Tab color palette (glassmorphism, index-based) ──────────────────────────
const TAB_COLORS = [
  { bg: 'rgba(99,102,241,0.38)',  bgHover: 'rgba(99,102,241,0.60)',  border: 'rgba(99,102,241,0.55)',  shadow: 'rgba(99,102,241,0.22)' },
  { bg: 'rgba(16,185,129,0.38)', bgHover: 'rgba(16,185,129,0.60)', border: 'rgba(16,185,129,0.55)', shadow: 'rgba(16,185,129,0.22)' },
  { bg: 'rgba(249,115,22,0.38)', bgHover: 'rgba(249,115,22,0.60)', border: 'rgba(249,115,22,0.55)', shadow: 'rgba(249,115,22,0.22)' },
  { bg: 'rgba(168,85,247,0.38)', bgHover: 'rgba(168,85,247,0.60)', border: 'rgba(168,85,247,0.55)', shadow: 'rgba(168,85,247,0.22)' },
  { bg: 'rgba(20,184,166,0.38)', bgHover: 'rgba(20,184,166,0.60)', border: 'rgba(20,184,166,0.55)', shadow: 'rgba(20,184,166,0.22)' },
  { bg: 'rgba(244,63,94,0.38)',  bgHover: 'rgba(244,63,94,0.60)',  border: 'rgba(244,63,94,0.55)',  shadow: 'rgba(244,63,94,0.22)' },
  { bg: 'rgba(245,158,11,0.38)', bgHover: 'rgba(245,158,11,0.60)', border: 'rgba(245,158,11,0.55)', shadow: 'rgba(245,158,11,0.22)' },
  { bg: 'rgba(14,165,233,0.38)', bgHover: 'rgba(14,165,233,0.60)', border: 'rgba(14,165,233,0.55)', shadow: 'rgba(14,165,233,0.22)' },
] as const

/** Inject glassmorphism color CSS vars into each tab via pocket index. */
function tabStyle(pocket: SdpPocket): Record<string, string> {
  const idx = visiblePockets.value.indexOf(pocket) % TAB_COLORS.length
  const c = TAB_COLORS[idx]
  return {
    '--tab-bg':       c.bg,
    '--tab-bg-hover': c.bgHover,
    '--tab-border':   c.border,
    '--tab-shadow':   c.shadow,
  }
}
</script>

<style scoped>
/* ───────────────────────────────────────────────
   Backdrop — semi-transparent, behind popups
──────────────────────────────────────────────── */
.sdp-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1099;
  background: rgba(0, 0, 0, 0.18);
}

/* ───────────────────────────────────────────────
   Strip — fixed container on the right edge
──────────────────────────────────────────────── */
.sdp-strip {
  position: fixed;
  right: 0;
  /* top is bound via inline style (calc(25% + stripYOffset)) */
  transform: translateY(-50%);
  z-index: 1100;
  display: flex;
  flex-direction: column;
  gap: 6px;
  /* Let click events pass through the strip background to the page */
  pointer-events: none;
}

/* ───────────────────────────────────────────────
   Widget wrapper
──────────────────────────────────────────────── */
.sdp-widget {
  position: relative;
  pointer-events: all;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  cursor: grab;
}

.sdp-widget:active {
  cursor: grabbing;
}

.sdp-strip--dragging .sdp-widget {
  cursor: grabbing;
}

/* Floating square button that marks detached popup drop placement. */
.sdp-drop-proxy {
  position: fixed;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  border: 1px solid rgba(99, 102, 241, 0.55);
  background: rgba(99, 102, 241, 0.36);
  backdrop-filter: blur(10px) saturate(1.5);
  -webkit-backdrop-filter: blur(10px) saturate(1.5);
  box-shadow:
    -4px 3px 18px rgba(99, 102, 241, 0.30),
    inset 0 1px 0 rgba(255, 255, 255, 0.22);
  display: flex;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  z-index: 1102;
}

.sdp-drop-proxy-icon {
  color: rgba(255, 255, 255, 0.98);
  font-size: 1rem;
}

/* ───────────────────────────────────────────────
   Tab button — bookmark with glassmorphism
   (color CSS vars injected per-tab via tabStyle())
──────────────────────────────────────────────── */
.sdp-tab {
  display: flex;
  align-items: center;
  /* Collapsed state: icon width + padding */
  max-width: 46px;
  min-width: 46px;
  height: 44px;
  padding: 0 10px 0 12px;
  overflow: hidden;
  white-space: nowrap;

  background: var(--tab-bg, rgba(99, 102, 241, 0.62));
  border: 1px solid var(--tab-border, rgba(99, 102, 241, 0.75));
  border-right: none;
  border-radius: 10px 0 0 10px;
  cursor: grab;
  color: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px) saturate(1.5);
  -webkit-backdrop-filter: blur(10px) saturate(1.5);
  box-shadow:
    -3px 2px 14px var(--tab-shadow, rgba(99, 102, 241, 0.28)),
    inset 0 1px 0 rgba(255, 255, 255, 0.22),
    inset 0 -1px 0 rgba(0, 0, 0, 0.08);

  transition:
    max-width 0.25s cubic-bezier(0.4, 0, 0.2, 1),
    background 0.15s ease,
    box-shadow 0.15s ease,
    color 0.15s ease;
}

.sdp-tab:active {
  cursor: grabbing;
}

/* Expanded on hover or when the popup is open */
.sdp-widget:hover .sdp-tab,
.sdp-widget--open .sdp-tab {
  max-width: 210px;
  background: var(--tab-bg-hover, rgba(99, 102, 241, 0.82));
  color: rgba(255, 255, 255, 0.98);
  box-shadow:
    -5px 3px 20px var(--tab-shadow, rgba(99, 102, 241, 0.35)),
    inset 0 1px 0 rgba(255, 255, 255, 0.28),
    inset 0 -1px 0 rgba(0, 0, 0, 0.10);
}

/* Pinned state: fully opaque accent */
.sdp-widget--pinned .sdp-tab {
  background: var(--tab-bg-hover, rgba(99, 102, 241, 0.90));
  border-color: var(--tab-border, rgba(99, 102, 241, 0.90));
  color: rgba(255, 255, 255, 0.98);
  box-shadow:
    -4px 3px 16px var(--tab-shadow, rgba(99, 102, 241, 0.40)),
    inset 0 1px 0 rgba(255, 255, 255, 0.30);
}

.sdp-tab-icon {
  font-size: 1.05rem;
  flex-shrink: 0;
  width: 22px;
  text-align: center;
}

.sdp-tab-label {
  font-size: 0.78rem;
  font-weight: 500;
  padding-left: 7px;
  padding-right: 4px;
  /* Fade in / out alongside the width expansion */
  opacity: 0;
  transition: opacity 0.15s ease;
}

.sdp-widget:hover .sdp-tab .sdp-tab-label,
.sdp-widget--open .sdp-tab .sdp-tab-label {
  opacity: 1;
}

/* ───────────────────────────────────────────────
   Popup panel — appears to the left of the tab
──────────────────────────────────────────────── */
.sdp-popup {
  position: fixed;
  /* top and left are set via inline style (absolute viewport coordinates) */
  width: 300px;
  max-height: min(420px, 80vh);
  display: flex;
  flex-direction: column;

  background: rgba(255, 255, 255, 0.18);
  backdrop-filter: blur(24px) saturate(1.8);
  -webkit-backdrop-filter: blur(24px) saturate(1.8);
  border: 1px solid rgba(255, 255, 255, 0.40);
  border-radius: 10px;
  box-shadow: -4px 4px 28px rgba(0, 0, 0, 0.18);
  z-index: 1101;
}
.sdp-popup-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.65rem 0.85rem;
  border-bottom: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 10px 10px 0 0;
  flex-shrink: 0;
  background: rgba(232, 240, 254, 0.08);
  cursor: move;
}

.sdp-popup-header:active {
  cursor: grabbing;
}

.sdp-popup--dragging {
  user-select: none;
  cursor: grabbing;
}

.sdp-popup-header-icon {
  font-size: 0.95rem;
  color: var(--primary-color, #6366f1);
  flex-shrink: 0;
}

.sdp-popup-title {
  flex: 1;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-color, #212121);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sdp-popup-header-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.sdp-icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0.35rem;
  border: none;
  background: transparent;
  cursor: pointer;
  border-radius: 5px;
  color: var(--text-color-secondary, #607d8b);
  font-size: 0.85rem;
  transition: background 0.12s, color 0.12s;
}

.sdp-icon-btn:hover {
  background: var(--surface-hover, #f0f0f0);
  color: var(--text-color, #212121);
}

.sdp-icon-btn--active {
  color: var(--primary-color, #6366f1);
  background: var(--primary-50, #eef2ff);
}

.sdp-icon-btn--active:hover {
  background: var(--primary-100, #e0e7ff);
}

.sdp-popup-body {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0.75rem;
  background: transparent;
}

.sdp-popup-footer {
  flex-shrink: 0;
  min-height: 8px;
  border-top: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 0 0 10px 10px;
  background: transparent;
}

/* ───────────────────────────────────────────────
   Transitions
──────────────────────────────────────────────── */
.sdp-popup-enter-active {
  transition: opacity 0.15s ease, transform 0.2s ease;
}
.sdp-popup-leave-active {
  transition: opacity 0.1s ease, transform 0.15s ease;
}
.sdp-popup-enter-from,
.sdp-popup-leave-to {
  opacity: 0;
  transform: translateX(8px);
}

.sdp-fade-enter-active,
.sdp-fade-leave-active {
  transition: opacity 0.2s ease;
}
.sdp-fade-enter-from,
.sdp-fade-leave-to {
  opacity: 0;
}
</style>
