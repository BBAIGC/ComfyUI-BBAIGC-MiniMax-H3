import { app } from '/scripts/app.js';

// The Vue node renderer mounts our live DOM but does not consume the legacy
// getMinHeight/getMaxHeight contract. Bound it from the owning graph geometry,
// never from the DOM's intrinsic content height (which creates a growth loop).
export function hideNativeWidget(widget) {
  if (!widget) return;
  widget.hidden = true;
  widget.options ||= {};
  widget.options.hidden = true;
  widget.options.canvasOnly = true;
  widget.options.hideInPanel = true;
}

const OWN_NODE = /^(?:H3|Codex|BBAPI)/;
const OWN_PANEL = /^(?:CODEX_|h3-|H3_SUBTITLES$)/;
const states = new WeakMap();
const CONTROL_PANEL = '.qws-controls[data-bbaigc-bounded-panel],.codex-control-style[data-bbaigc-bounded-panel]';
let scrollCleanup = null;

function controlWheel(event) {
  if (event.ctrlKey || event.metaKey || (!event.deltaX && !event.deltaY)) return;
  const target = event.target instanceof Element ? event.target : null;
  const panel = target?.closest(CONTROL_PANEL);
  if (!panel) return;
  const unit = event.deltaMode === 1 ? 18 : event.deltaMode === 2 ? Math.max(1, panel.clientHeight) : 1;
  const horizontal = Boolean(event.shiftKey || Math.abs(event.deltaX) > Math.abs(event.deltaY));
  const delta = (horizontal ? event.deltaX || event.deltaY : event.deltaY) * unit;
  const position = horizontal ? 'scrollLeft' : 'scrollTop';
  const content = horizontal ? 'scrollWidth' : 'scrollHeight';
  const client = horizontal ? 'clientWidth' : 'clientHeight';
  for (let element = target; element && panel.contains(element); element = element.parentElement) {
    const css = getComputedStyle(element);
    if (!/auto|scroll/.test(horizontal ? css.overflowX : css.overflowY)) continue;
    const maximum = Math.max(0, element[content] - element[client]);
    const previous = element[position] || 0;
    const next = Math.max(0, Math.min(maximum, previous + delta));
    if (next !== previous) { element[position] = next; break; }
    if (element === panel) break;
  }
  // TransformPane forwards wheel in its capture phase. A root bubble listener
  // arrives too late, so handle the bounded panel before that ancestor runs.
  // Consume boundary wheels too: reaching the last item must not zoom canvas.
  event.preventDefault();
  event.stopImmediatePropagation();
}

function installControlScrolling() {
  scrollCleanup?.();
  let sheet = document.getElementById('bbaigc-control-scroll-style');
  if (!sheet) {
    sheet = document.createElement('style');
    sheet.id = 'bbaigc-control-scroll-style';
    sheet.textContent = `
      .qws-controls[data-bbaigc-bounded-panel],.codex-control-style[data-bbaigc-bounded-panel]{overflow-y:auto!important;overflow-x:hidden!important;overscroll-behavior:contain;scrollbar-gutter:stable;scrollbar-width:auto;scrollbar-color:#969696 #292929}
      .qws-controls[data-bbaigc-bounded-panel]>.qws-column{min-height:0;max-height:100%;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;scrollbar-width:auto;scrollbar-color:#969696 #292929}
      .codex-control-style[data-bbaigc-bounded-panel]>*{flex-shrink:0!important}
      .codex-control-style[data-bbaigc-bounded-panel] .codex-control-fold-body>*{flex-shrink:0}
      .qws-controls[data-bbaigc-bounded-panel]::-webkit-scrollbar,.qws-controls[data-bbaigc-bounded-panel]>.qws-column::-webkit-scrollbar,.codex-control-style[data-bbaigc-bounded-panel]::-webkit-scrollbar{width:12px;height:12px}
      .qws-controls[data-bbaigc-bounded-panel]::-webkit-scrollbar-track,.qws-controls[data-bbaigc-bounded-panel]>.qws-column::-webkit-scrollbar-track,.codex-control-style[data-bbaigc-bounded-panel]::-webkit-scrollbar-track{background:#292929}
      .qws-controls[data-bbaigc-bounded-panel]::-webkit-scrollbar-thumb,.qws-controls[data-bbaigc-bounded-panel]>.qws-column::-webkit-scrollbar-thumb,.codex-control-style[data-bbaigc-bounded-panel]::-webkit-scrollbar-thumb{background:#969696;border:3px solid #292929;border-radius:6px}
    `;
    document.head.append(sheet);
  }
  document.addEventListener('wheel', controlWheel, { capture: true, passive: false });
  scrollCleanup = () => {
    document.removeEventListener('wheel', controlWheel, true);
    window.removeEventListener('pagehide', scrollCleanup);
  };
  window.addEventListener('pagehide', scrollCleanup, { once: true });
}

function readHeight(value, fallback = 0) {
  try {
    const number = Number(typeof value === 'function' ? value() : value);
    return Number.isFinite(number) && number >= 0 ? number : fallback;
  } catch { return fallback; }
}

function sizeOf(node, state, requested) {
  const height = Number(requested?.[1] ?? node.size?.[1]);
  return Number.isFinite(height) && height > 0 ? height : state.height || 500;
}

function update(node, requested) {
  const state = states.get(node);
  if (!state || state.disposed) return;
  state.height = sizeOf(node, state, requested);
  const visibleNative = (node.widgets || []).filter(widget => !widget.element && !widget.hidden && widget.options?.hidden !== true && !String(widget.type).includes('hidden')).length;
  const h3Workspace = state.widgets.some(item => ['h3-studio','h3-manual-images','h3-dialogue-studio'].includes(item.kind));
  const inputRows = h3Workspace ? (node.inputs || []).filter(input => {
    const name = input.widget?.name || input.name;
    const widget = node.widgets?.find(candidate => candidate.name === name);
    // H3 replaces its schema controls with one DOM workspace. Hidden widget
    // sockets do not consume independent Vue rows and must not reserve space
    // for every backend parameter a second time.
    return !widget || (!widget.hidden && widget.options?.hidden !== true);
  }).length : node.inputs?.length || 0;
  const sockets = Math.max(inputRows, node.outputs?.length || 0);
  let reserve = h3Workspace ? Math.max(75, 35 + visibleNative * 28 + sockets * 20) : Math.max(75, 35 + visibleNative * 28 + sockets * 10);
  const host = state.widgets[0]?.element.closest?.('[data-node-id]');
  reserve = state.vueReserve ?? reserve;
  const flexible = state.widgets.filter(item => item.fixed == null);
  const minimums = flexible.map(item => readHeight(item.options.getMinHeight, item.minimum));
  const fixedHeight = state.widgets.reduce((sum, item) => sum + (item.fixed || 0), 0);
  const extra = Math.max(0, state.height - reserve - fixedHeight - minimums.reduce((a, b) => a + b, 0));
  for (const item of state.widgets) {
    const minimum = readHeight(item.options.getMinHeight, item.minimum);
    const maximum = readHeight(item.options.getMaxHeight, Infinity);
    const index = flexible.indexOf(item);
    const height = item.fixed != null ? readHeight(item.options.getMinHeight, item.fixed) : Math.max(minimum, Math.min(maximum, minimum + (index >= 0 ? extra / flexible.length : 0)));
    // Vue's corner resizer temporarily sets --node-height to 0 to measure the
    // minimum content height. A saved pixel height here makes that measurement
    // include the old workspace, so every drag can only grow the node. Follow
    // the inherited graph height while retaining a pixel fallback for canvas.
    const available = `calc((var(--node-height, ${Math.round(state.height)}px) - ${reserve + fixedHeight + minimums.reduce((a, b) => a + b, 0)}px) / ${flexible.length || 1} + ${minimum}px)`;
    const value = item.fixed != null ? `${Math.round(height)}px`
      : `max(${minimum}px, ${Number.isFinite(maximum) ? `min(${maximum}px, ${available})` : available})`;
    // Important prevents WidgetDOM's `*:flex-1` from expanding this panel to
    // its entire intrinsic content. Existing panel columns keep their scroll.
    item.element.style.setProperty('height', value, 'important');
    item.element.style.setProperty('max-height', value, 'important');
    item.element.style.setProperty('min-height', '0', 'important');
    item.element.style.setProperty('flex', '0 0 auto', 'important');
    item.element.style.setProperty('box-sizing', 'border-box');
    item.element.style.setProperty('overflow', 'auto');
    item.element.dataset.bbaigcBoundedPanel = 'true';
  }
  if (host?.isConnected && state.vueReserve == null) {
    const saved = host.style.getPropertyValue('--node-height');
    // Measure only Vue's sockets, title, padding and widget gaps after the
    // bounded styles exist. Removing its saved minimum prevents blank node
    // space from being mistaken for chrome and keeping the panel small.
    host.style.setProperty('--node-height', '0px');
    const panelHeight = state.widgets.reduce((sum, item) => sum + item.element.offsetHeight, 0);
    state.vueReserve = Math.max(reserve, host.offsetHeight - panelHeight);
    host.style.setProperty('--node-height', saved);
    if (state.vueReserve !== reserve) update(node, requested);
  }
}

function schedule(node, requested) {
  const state = states.get(node);
  if (!state || state.disposed) return;
  if (requested) state.requested = Array.from(requested);
  if (state.frame) return;
  state.frame = requestAnimationFrame(() => {
    state.frame = null;
    const size = state.requested;
    state.requested = null;
    update(node, size);
  });
}

function bind(node, widget, element, options, kind) {
  if (!element?.style || widget.hidden) return;
  let state = states.get(node);
  if (!state) {
    state = { widgets: [], height: Number(node.size?.[1]) || 500, frame: null, requested: null, disposed: false };
    states.set(node, state);
    const resize = node.onResize;
    node.onResize = function(size) {
      const result = resize?.apply(this, arguments);
      update(node, size);
      return result;
    };
    const configure = node.onConfigure;
    node.onConfigure = function(configuration) {
      const result = configure?.apply(this, arguments);
      schedule(node, configuration?.size || node.size);
      return result;
    };
    const setSize = node.setSize;
    if (typeof setSize === 'function') node.setSize = function(size) {
      const result = setSize.apply(this, arguments);
      // Vue node resizing and workspace layout switches can update the graph
      // size without invoking the legacy onResize callback. Re-read the final
      // graph geometry after those operations instead of a stale saved height.
      schedule(node, node.size || size);
      return result;
    };
    const removed = node.onRemoved;
    node.onRemoved = function() {
      state.disposed = true;
      if (state.frame) cancelAnimationFrame(state.frame);
      states.delete(node);
      return removed?.apply(this, arguments);
    };
  }
  const minimum = readHeight(options.getMinHeight, ({ 'h3-studio': 440, 'h3-manual-images': 500, 'h3-dialogue-studio': 460, 'h3-voices': 440, 'h3-rules': 1180 })[kind] || 90);
  const max = readHeight(options.getMaxHeight, Infinity);
  const fixed = (max === minimum && max < Infinity) ? minimum : ({ 'h3-voices': 440, 'h3-rules': 1180 })[kind] ?? null;
  state.widgets.push({ widget, element, options, kind, minimum, fixed });
  state.vueReserve = null;
  update(node);
  schedule(node);
}

function focusWorkspace(graph) {
  if (!graph || app.graph !== graph || !app.canvas?.canvas) return;
  const nodes = graph._nodes || [];
  const h3 = nodes.find(node => node.type === 'H3StoryboardStudio' && !node.flags?.collapsed);
  const imageTypes = new Set(['CodexQwenStudioControls', 'CodexQwenStudioReferences', 'CodexQwenStudioPrompt', 'CodexQwenStudioResults']);
  const image = nodes.filter(node => imageTypes.has(node.type) && !node.flags?.collapsed);
  // Only the two integrated workspaces own their opening view. Leave ordinary
  // graphs, backend nodes and every saved collapsed flag untouched.
  const panels = h3 ? [h3] : image.length === imageTypes.size ? image : [];
  if (!panels.length) return;
  const left = Math.min(...panels.map(node => node.pos[0]));
  const top = Math.min(...panels.map(node => node.pos[1] - 30));
  const right = Math.max(...panels.map(node => node.pos[0] + node.size[0]));
  const bottom = Math.max(...panels.map(node => node.pos[1] + node.size[1]));
  app.canvas.ds.fitToBounds([left - 35, top - 35, right - left + 70, bottom - top + 70], { zoom: 0.9 });
  app.canvas.setDirty(true, true);
}

function installDesktopMcpIcon() {
  if (document.getElementById('bbaigc-desktop-mcp-icon')) return;
  const style = document.createElement('style');
  style.id = 'bbaigc-desktop-mcp-icon';
  // Desktop injects this Iconify class, but recent frontends do not ship its
  // CSS. A scoped mask keeps the original button, tooltip and click handler.
  const icon = 'url("data:image/svg+xml,%3Csvg xmlns=%27http://www.w3.org/2000/svg%27 viewBox=%270 0 24 24%27 fill=%27none%27 stroke=%27black%27 stroke-width=%272%27 stroke-linecap=%27round%27 stroke-linejoin=%27round%27%3E%3Cpath d=%27M12 22v-5M9 8V2M15 8V2M7 8h10v3a5 5 0 0 1-10 0Z%27/%3E%3C/svg%3E")';
  style.textContent = `#comfy-desktop-mcp-btn .side-bar-button-icon{display:inline-block;width:20px;height:20px;flex:none;background-color:currentColor;-webkit-mask:${icon} center/contain no-repeat;mask:${icon} center/contain no-repeat}`;
  document.head.append(style);
}

app.registerExtension({
  name: 'BBAIGC.Nodes2DOMCompatibility',
  setup() { installControlScrolling(); installDesktopMcpIcon(); },
  afterConfigureGraph() {
    const graph = app.graph;
    // Native loading restores the saved camera after configuration callbacks.
    requestAnimationFrame(() => requestAnimationFrame(() => focusWorkspace(graph)));
  },
  beforeRegisterNodeDef(type, data) {
    if (!OWN_NODE.test(data.name)) return;
    const add = type.prototype.addDOMWidget;
    if (!add || Object.hasOwn(type.prototype, '_bbaigcDOMBounded')) return;
    type.prototype._bbaigcDOMBounded = true;
    type.prototype.addDOMWidget = function(name, kind, element, options = {}) {
      const widget = add.call(this, name, kind, element, options);
      if (OWN_PANEL.test(kind)) bind(this, widget, element, options, kind);
      return widget;
    };
  },
});
