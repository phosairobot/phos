"use strict";
 (() => {
  const button = document.querySelector("[data-copy-credential]"), credential = document.getElementById("issued-credential");
  if (!button || !credential || !navigator.clipboard) return;
  button.addEventListener("click", () => { navigator.clipboard.writeText(credential.value); });
})();

// The Agents page keeps all mutations in ordinary CSRF-protected forms.  This
// controller only manages transient presentation: native dialogs and menus.
(() => {
  const page = document.querySelector("[data-agents-page]");
  if (!page) return;
  const menus = () => [...page.querySelectorAll("details[data-agent-transient]")];
  const closeMenus = (except = null) => menus().forEach((menu) => {
    if (menu !== except) menu.open = false;
  });
  const closeDialogs = (except = null) => page.querySelectorAll("dialog[open]").forEach((dialog) => {
    if (dialog !== except) dialog.close();
  });
  page.querySelectorAll("[data-agent-open-dialog]").forEach((button) => button.addEventListener("click", () => {
    const dialog = document.getElementById(button.dataset.agentOpenDialog);
    if (!dialog) return;
    closeMenus(); closeDialogs(dialog);
    dialog.querySelector("form")?.reset();
    // Edit forms need their server-rendered values, so reset only restores
    // defaults; create starts blank because its controls have no defaults.
    dialog.showModal();
  }));
  page.querySelectorAll("[data-agent-close-dialog]").forEach((button) => button.addEventListener("click", () => {
    button.closest("dialog")?.close();
  }));
  page.querySelectorAll("[data-agent-skin-controls]").forEach((controls) => {
    const enabled = controls.querySelector("[data-agent-skin-enabled]");
    const select = controls.querySelector("[data-agent-skin-select]");
    const stored = controls.querySelector("[data-agent-skin-id]");
    if (!enabled || !select || !stored) return;
    const sync = () => {
      select.disabled = !enabled.checked;
      if (select.value) stored.value = select.value;
    };
    enabled.addEventListener("change", sync);
    select.addEventListener("change", sync);
    controls.closest("form")?.addEventListener("reset", () => setTimeout(sync));
    sync();
  });
  menus().forEach((menu) => menu.addEventListener("toggle", () => {
    if (menu.open) { closeMenus(menu); closeDialogs(); }
  }));
  document.addEventListener("pointerdown", (event) => {
    if (!page.contains(event.target) || !event.target.closest("details[data-agent-transient]")) closeMenus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") { closeMenus(); closeDialogs(); }
  });
})();

(() => {
  const controls = document.querySelector("[data-iris-color-controls]");
  if (!controls) return;
  const preset = controls.querySelector("[data-iris-color-preset]");
  const picker = controls.querySelector("[data-iris-color-picker]");
  const hex = controls.querySelector("[data-iris-color-hex]");
  const stored = controls.querySelector('input[name="display.iris_color"]');
  const error = controls.querySelector("[data-iris-color-error]");
  const form = controls.closest("form");
  const valid = (value) => /^#[0-9a-fA-F]{6}$/.test(value);
  let lastCustom = valid(stored.value) ? stored.value.toUpperCase() : "#28CEEB";
  const setValue = (value) => {
    const normalized = value.toUpperCase();
    stored.value = normalized; hex.value = normalized; picker.value = normalized;
    const okay = valid(normalized);
    error.hidden = okay;
    form.querySelector("button.save")?.toggleAttribute("disabled", !okay);
  };
  const updateMode = () => {
    const custom = preset.value === "custom";
    picker.disabled = !custom; hex.readOnly = !custom;
    if (custom) setValue(lastCustom);
    else setValue(preset.selectedOptions[0].dataset.color);
  };
  preset.addEventListener("change", updateMode);
  picker.addEventListener("input", () => { lastCustom = picker.value.toUpperCase(); setValue(lastCustom); });
  hex.addEventListener("input", () => {
    const value = hex.value;
    if (valid(value)) { lastCustom = value.toUpperCase(); setValue(lastCustom); }
    else { stored.value = value; error.hidden = false; form.querySelector("button.save")?.setAttribute("disabled", ""); }
  });
  updateMode();
})();

// MCP authentication remains an ordinary server-validated form. This only
// reveals the anonymous-only controls; hidden controls keep their values.
(() => {
  const mode = document.getElementById("mcp.authentication.mode");
  if (!mode) return;
  const profile = document.querySelector("[data-mcp-anonymous-profile]");
  const warning = document.querySelector("[data-mcp-anonymous-warning]");
  const confirmation = document.querySelector("[data-mcp-anonymous-confirm]");
  const mixedNote = document.querySelector("[data-mcp-mixed-note]");
  const update = () => {
    const anonymous = mode.value === "none" || mode.value === "mixed";
    if (profile) profile.hidden = !anonymous;
    if (warning) warning.hidden = !anonymous;
    if (confirmation) confirmation.hidden = !anonymous;
    if (mixedNote) mixedNote.hidden = mode.value !== "mixed";
  };
  mode.addEventListener("change", update);
  update();
})();

const provider = document.getElementById("expression.provider");
if (provider) {
  const update = () => {
    for (const name of ["local", "aws"]) {
      for (const section of document.querySelectorAll(`[data-provider="${name}"]`)) {
        section.hidden = provider.value !== name;
      }
    }
  };
  provider.addEventListener("change", update);
  // Show both provider sections after validation errors so inactive settings
  // can also be corrected without losing the selected provider.
  if (!document.querySelector(".error")) update();
}

const sttProvider = document.getElementById("voice.stt.provider");
if (sttProvider) {
  const section = sttProvider.closest(".voice-section--stt");
  const update = () => {
    for (const panel of section.querySelectorAll("[data-stt-provider-panel]")) {
      panel.hidden = sttProvider.value !== panel.dataset.sttProviderPanel;
    }
  };
  sttProvider.addEventListener("change", update);
  if (!document.querySelector(".error")) update();
}

const ttsProvider = document.getElementById("tts.provider");
if (ttsProvider) {
  const section = ttsProvider.closest(".voice-section--tts");
  const update = () => {
    for (const panel of section.querySelectorAll("[data-tts-provider-panel]")) {
      panel.hidden = ttsProvider.value !== panel.dataset.ttsProviderPanel;
    }
  };
  ttsProvider.addEventListener("change", update);
  if (!document.querySelector(".error")) update();
}

// Controller diagnostics are intentionally page-local: one bounded read every
// five seconds while this page is open, never part of the dashboard snapshot.
(() => {
  const panel = document.querySelector("[data-controller-diagnostics]");
  if (!panel) return;
  const rows = panel.querySelector("[data-controller-message-rows]");
  const messageScroll = panel.querySelector("[data-controller-message-scroll]");
  const filters = panel.querySelector("[data-controller-filters]");
  const type = filters.querySelector('[name="message_type"]');
  const direction = filters.querySelector('[name="direction"]');
  const heartbeat = filters.querySelector('[name="show_heartbeat"]');
  let messages = [];
  const selected = () => messages.filter((item) =>
    (heartbeat.checked || item.type !== "heartbeat") &&
    (!type.value || item.type === type.value) && (!direction.value || item.direction === direction.value));
  const renderStatus = (status) => {
    const fallback = {active_device: "None", protocol: "No valid controller message received", last_heartbeat: "None"};
    for (const node of panel.querySelectorAll("[data-controller-status]")) {
      const value = status && status[node.dataset.controllerStatus];
      node.textContent = value === null || value === undefined || value === "" ? (fallback[node.dataset.controllerStatus] || "Unavailable") : String(value);
    }
  };
  const render = () => {
    const scrollTop = messageScroll.scrollTop;
    rows.replaceChildren();
    const visible = selected();
    if (!visible.length) {
      const row = document.createElement("tr"), cell = document.createElement("td");
      cell.colSpan = 4; cell.textContent = messages.length ? "No messages match the current filters." : "No controller messages received yet.";
      row.append(cell); rows.append(row); messageScroll.scrollTop = scrollTop; return;
    }
    for (const item of [...visible].reverse()) {
      const row = document.createElement("tr");
      for (const value of [item.timestamp, item.direction, item.type]) {
        const cell = document.createElement("td"); cell.textContent = value || ""; row.append(cell);
      }
      const summary = document.createElement("td"); summary.textContent = item.summary || "";
      if (item.raw) { const details = document.createElement("details"), label = document.createElement("summary"), raw = document.createElement("pre"); label.textContent = "Raw"; raw.textContent = item.raw; details.append(label, raw); summary.append(details); }
      row.append(summary); rows.append(row);
    }
    messageScroll.scrollTop = scrollTop;
  };
  const refresh = async () => {
    try {
      const response = await fetch(panel.dataset.endpoint, {credentials: "same-origin", headers: {Accept: "application/json"}});
      if (!response.ok) return;
      const data = await response.json();
      messages = Array.isArray(data.messages) ? data.messages : [];
      renderStatus(data.status);
      const known = new Set([...type.options].map((option) => option.value));
      for (const item of messages) if (item.type && !known.has(item.type)) { const option = new Option(item.type, item.type); type.add(option); known.add(item.type); }
      render();
    } catch (_) { /* Retain the last rendered bounded snapshot on a transient failure. */ }
  };
  panel.querySelector("[data-controller-filter]").addEventListener("click", render);
  filters.addEventListener("change", render);
  panel.querySelector("[data-controller-clear]").addEventListener("submit", async (event) => {
    event.preventDefault();
    await fetch(event.currentTarget.action, {method: "POST", body: new FormData(event.currentTarget), credentials: "same-origin"});
    messages = []; render();
  });
  refresh(); window.setInterval(refresh, 5000);
})();

(() => {
  const form = document.querySelector("[data-configuration-editor]");
  if (!form) return;
  const editor = form.querySelector("textarea");
  let dirty = form.dataset.editorDirty === "true", submitting = false;
  editor.addEventListener("input", () => { dirty = true; });
  form.addEventListener("submit", (event) => {
    submitting = true;
    if (event.submitter && event.submitter.value === "save") dirty = false;
  });
  window.addEventListener("beforeunload", (event) => {
    if (!dirty || submitting) return;
    event.preventDefault(); event.returnValue = "";
  });
})();

// The administration UI consumes only the authenticated Remote API's semantic
// models.  Waitress exposes /events as SSE; EventSource carries session cookies
// and keeps this browser tab to one low-rate connection.
(() => {
  const dashboard = document.querySelector("[data-phos-live]");
  if (!dashboard || !window.EventSource) return;
  const indicator = document.getElementById("phos-connection");
  const note = dashboard.querySelector(".live-note");
  const error = dashboard.querySelector(".live-error");
  const success = dashboard.querySelector(".live-success");
  const state = {};
  let capabilities;
  let stream, retryTimer, retries = 0, fallbackTimer, opened = false;
  const endpoint = "/api/v1";
  // Capabilities publish canonical API paths; local dashboard reads use short
  // paths.  Accept both without ever producing /api/v1/api/v1/….
  const apiUrl = (path) => path.startsWith(`${endpoint}/`) ? path : endpoint + path;
  const setConnection = (name) => {
    indicator.className = `connection ${name}`;
    indicator.textContent = `Live: ${name}`;
  };
  const display = (key, value) => {
    for (const node of dashboard.querySelectorAll(`[data-live="${key}"]`)) {
      const text = value === null || value === undefined || value === "" ? "Unavailable" : String(value);
      if (node.textContent !== text) node.textContent = text;
    }
  };
  const status = (item) => item && (item.status || item.state) || "unavailable";
  const age = (item) => {
    if (!item) return "Unavailable";
    const timestamp = Date.parse(item.last_update);
    const seconds = Number.isFinite(timestamp) ? Math.max(0, Math.floor((Date.now() - timestamp) / 1000))
      : Number.isFinite(item.age_seconds) ? Math.max(0, Math.floor(item.age_seconds)) : null;
    return seconds === null ? "Unavailable" : `${seconds}s ago`;
  };
  // ENS160/AHT21 publish field-level usability because their individual
  // measurements may have different validity/freshness.  Established sensor
  // snapshots such as BMP280 and MPU6050 use their aggregate `available`
  // contract instead; requiring a non-existent field map hid valid readings.
  const reading = (item, field, suffix) => item && item.available &&
    (!item.measurement_usable || item.measurement_usable[field] === true) &&
    item.measurements && item.measurements[field] !== null && item.measurements[field] !== undefined
    ? `${item.measurements[field]} ${suffix}` : "Unavailable";
  // ENS160 validity governs whether a measurement is safe for environmental
  // decisions, not whether the controller acquired it.  The dashboard keeps
  // fresh non-normal samples visible as explicitly unreliable observations.
  const ens160Reading = (item, field, suffix) => {
    const value = item && item.measurements && item.measurements[field];
    if (value === null || value === undefined) return "Unavailable";
    if (item.measurement_fresh && item.measurement_fresh[field] !== true) return "Stale";
    if (item.ens160_status_fresh === false) return "Stale";
    return `${value} ${suffix}`;
  };
  const ens160Validity = (item) => {
    const validity = item && item.ens160_validity;
    const labels = {0: "Normal", 1: "Warm-up", 2: "Initial startup", 3: "Invalid output"};
    if (!Object.hasOwn(labels, validity)) return {label: "Unknown", reliability: "Reliability unavailable"};
    if (item.ens160_status_fresh !== true) return {label: labels[validity], reliability: "Status stale — readings unreliable"};
    return validity === 0 ? {label: labels[validity], reliability: "Reliable"}
      : {label: labels[validity], reliability: "Observational — unreliable"};
  };
  const aqiUba = (item) => {
    const value = item && item.measurements && item.measurements.aqi_uba;
    const labels = {1: "Excellent", 2: "Good", 3: "Moderate", 4: "Poor", 5: "Unhealthy"};
    const observed = ens160Reading(item, "aqi_uba", "");
    if (observed === "Stale") return observed;
    return Object.hasOwn(labels, value) ? `${value} / 5 — ${labels[value]}` : "Unavailable";
  };
  const healthSummary = (health) => {
    const subsystems = health && health.subsystems;
    if (!subsystems || typeof subsystems !== "object") return "Unavailable";
    const values = Object.entries(subsystems).map(([name, value]) =>
      `${name.replaceAll("_", " ")}: ${(value && (value.state || value.status)) || "unavailable"}`);
    return values.length ? values.join(" · ") : "Unavailable";
  };
  const mergeSensor = (current, next) => next === undefined ? current : {
    ...(current || {}), ...next,
    measurements: next.measurements === undefined ? (current || {}).measurements : {
      ...((current || {}).measurements || {}), ...(next.measurements || {})
    }
  };
  const mergeSensors = (current, next) => ({
    ...(current || {}), ...(next || {}),
    environmental: mergeSensor((current || {}).environmental, (next || {}).environmental),
    ens160: mergeSensor((current || {}).ens160, (next || {}).ens160),
    aht21: mergeSensor((current || {}).aht21, (next || {}).aht21),
    imu: mergeSensor((current || {}).imu, (next || {}).imu)
  });
  const renderFreshness = () => {
    const sensors = state.sensors || {};
    display("environment.age", age(state.environment || sensors.environmental));
    display("ens160.age", age(sensors.ens160));
    display("motion.age", age(state.motion || sensors.imu));
  };
  const render = () => {
    const visual = state.visual || {};
    const environment = state.environment || {};
    // motion_update is a semantic transition and arrives ahead of the next
    // aggregate sensors refresh. Prefer its current snapshot over a stale
    // sensors.imu object retained from the initial Dashboard response.
    const motion = state.motion || (state.sensors || {}).imu || {};
    const ens160 = state.ens160 || (state.sensors || {}).ens160 || {};
    const aht21 = state.aht21 || (state.sensors || {}).aht21 || {};
    const health = state.health || {};
    const overlay = (state.overlay || {}).resolved || {};
    const presence = state.presence || {};
    const attention = state.attention || {};
    const target = attention.target || {};
    const observedExpression = state.observed_expression || {};
    const expressionReaction = state.expression_reaction || {};
    const touch = state.touch || {};
    const voice = state.voice || {};
    display("robot.state", (state.robot || {}).state);
    display("visual.expression", visual.expression);
    display("visual.source", state.active_visual_source || visual.source);
    display("motion.motion_state", motion.motion_state || visual.motion_state);
    display("motion.intensity", motion.motion_intensity && motion.motion_intensity.replaceAll("_", " "));
    display("environment.state", environment.state || ((state.sensors || {}).environmental_behavior || {}).state);
    display("environment.status", status(environment)); display("environment.temperature", reading(environment, "temperature_c", "°C"));
    display("environment.pressure", reading(environment, "pressure_hpa", "hPa"));
    display("aht21.humidity", reading(aht21, "humidity_percent", "% RH"));
    const validity = ens160Validity(ens160);
    display("ens160.status", status(ens160)); display("ens160.eco2", ens160Reading(ens160, "eco2_ppm", "ppm"));
    display("ens160.tvoc", ens160Reading(ens160, "tvoc_ppb", "ppb"));
    display("ens160.aqi", aqiUba(ens160));
    display("ens160.validity", validity.label); display("ens160.reliability", validity.reliability);
    display("motion.status", status(motion));
    display("health.summary", healthSummary(health));
    renderFreshness();
    display("overlay.resolved", `${overlay.temperature || "none"} / ${overlay.air_quality || "none"}`);
    const manual = (state.overlay || {}).override || (state.overlay || {}).manual || {};
    display("overlay.override", Object.keys(manual).length ? `${manual.temperature || "none"} / ${manual.air_quality || "none"}` : "None");
    display("presence.state", presence.state);
    display("presence.people_count", presence.people_count);
    display("attention.state", attention.state);
    display("attention.target_id", target.id);
    display("attention.target_position", target.x === null || target.x === undefined || target.y === null || target.y === undefined ? null : `${target.x}, ${target.y}`);
    display("attention.target_confidence", target.confidence);
    display("observed_expression.label", observedExpression.label);
    display("observed_expression.confidence", Number.isFinite(observedExpression.confidence) ? `${(observedExpression.confidence * 100).toFixed(0)}%` : null);
    display("observed_expression.provider", observedExpression.provider === "local" ? "Local ONNX" : observedExpression.provider);
    display("observed_expression.model", observedExpression.model);
    display("observed_expression.available", observedExpression.available === undefined ? null : (observedExpression.available ? "Available" : "Unavailable"));
    display("observed_expression.detail", observedExpression.available === false ? "No valid facial-expression observation" : null);
    display("observed_expression.observed_at", observedExpression.observed_at);
    display("expression_reaction.active", expressionReaction.active === undefined ? null : (expressionReaction.active ? "Active" : "Inactive"));
    display("expression_reaction.reaction", expressionReaction.reaction);
    display("expression_reaction.observed_label", expressionReaction.observed_label);
    display("touch.enabled", touch.enabled === undefined ? null : (touch.enabled ? "Enabled" : "Disabled"));
    display("touch.last_event", touch.last_event && touch.last_event.replaceAll("_", " "));
    display("touch.position", touch.x === null || touch.x === undefined || touch.y === null || touch.y === undefined ? null : `${touch.x}, ${touch.y}`);
    display("touch.normalized_position", Number.isFinite(touch.normalized_x) && Number.isFinite(touch.normalized_y) ? `${touch.normalized_x.toFixed(2)}, ${touch.normalized_y.toFixed(2)}` : null);
    display("touch.duration", Number.isFinite(touch.duration_ms) ? `${touch.duration_ms} ms` : null);
    display("touch.last_event_at", touch.last_event_at);
    display("voice.enabled", voice.enabled === undefined ? null : (voice.enabled ? "Enabled" : "Disabled"));
    display("voice.state", voice.state); display("voice.stt_provider", voice.stt_provider);
    display("voice.stt_available", voice.stt_available === undefined ? null : (voice.stt_available ? "Available" : "Unavailable"));
    display("voice.speech_detected", voice.speech_detected ? "Detected" : "Not detected");
    display("voice.last_transcript", voice.last_transcript); display("voice.last_confidence", Number.isFinite(voice.last_confidence) ? `${(voice.last_confidence * 100).toFixed(0)}%` : null);
    display("voice.language", voice.language); display("voice.last_error", voice.last_error);
    const subsystems = ((state.health || {}).subsystems) || {};
    display("health", Object.entries(subsystems).map(([name, item]) => `${name}: ${item.state}`).join(", "));
    if (note) note.textContent = "Semantic runtime state; retained while reconnecting.";
  };
  const fetchJson = async (path) => {
    const response = await fetch(apiUrl(path), {credentials: "same-origin", headers: {Accept: "application/json"}});
    return readJson(response, `Snapshot unavailable (${response.status})`);
  };
  const readJson = async (response, fallback) => {
    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      if (response.status === 401 || response.status === 403 || response.redirected) {
        throw new Error("Your administrator session has expired. Sign in again, then reload this page.");
      }
      throw new Error(`${fallback}; PHOS returned HTML instead of its API response.`);
    }
    const result = await response.json();
    if (!response.ok) throw new Error(result.error && result.error.message || fallback);
    return result;
  };
  const snapshot = async ({refreshCapabilities = false} = {}) => {
    try {
      // Read models are fetched together, then controls are populated from the
      // stable capability document before current values are selected.  None
      // of these initialization reads sends a semantic command.
      const requests = [fetchJson("/status"), fetchJson("/health"), fetchJson("/environment"),
                        fetchJson("/motion"), fetchJson("/overlay")];
      if (refreshCapabilities || !capabilities) requests.push(fetchJson("/capabilities"));
      const values = await Promise.all(requests);
      const [current, health, environment, motion, overlay, nextCapabilities] = values;
      if (nextCapabilities) capabilities = nextCapabilities;
      Object.assign(state, current, {health, environment, motion, overlay}); configure(capabilities); render(); syncControls();
      if (error) error.hidden = true;
    } catch (problem) {
      if (error) { error.textContent = problem.message; error.hidden = false; }
    }
  };
  const configure = (capabilities) => {
    const controls = dashboard.querySelector("[data-live-controls]");
    if (!capabilities || !controls || controls.dataset.ready) return;
    const commands = capabilities.commands || {};
    for (const node of controls.querySelectorAll("[data-command]")) {
      const command = commands[node.dataset.command]; if (!command) { node.disabled = true; continue; }
      for (const value of command.allowed_values || []) node.add(new Option(value, value));
      node.addEventListener("change", () => { node.dataset.dirty = "true"; });
      const apply = controls.querySelector(`[data-command-apply="${node.dataset.command}"]`);
      if (!apply) { node.disabled = true; continue; }
      if (!apply.dataset.bound) {
        apply.addEventListener("click", async () => {
          const result = await commandRequest(command.endpoint, {[command.field]: node.value}, "POST", apply.textContent, apply);
          if (!result) return;
          delete node.dataset.dirty;
          // The command response is authoritative for this just-applied value;
          // a concurrent background snapshot must not put the old value back.
          if (node.dataset.command === "set_visual_source" && result.source) state.active_visual_source = result.source;
          syncControls();
        });
        apply.dataset.bound = "true";
      }
    }
    const overlay = commands.set_overlay && commands.set_overlay.fields;
    for (const node of controls.querySelectorAll("select[data-overlay]")) {
      const field = overlay && overlay[node.dataset.overlay]; if (!field) { node.disabled = true; continue; }
      for (const value of field.allowed_values || []) node.add(new Option(value, value));
    }
    controls.querySelector("[data-overlay-submit]").addEventListener("click", async () => {
      const payload = {}; for (const node of controls.querySelectorAll("[data-overlay]")) payload[node.dataset.overlay] = node.type === "number" ? Number(node.value) : node.value;
      await commandRequest("/overlay", payload, "POST", "Overlay applied", controls.querySelector("[data-overlay-submit]"));
    });
    controls.querySelector("[data-overlay-clear]").addEventListener("click", async () => { await commandRequest("/overlay", undefined, "DELETE", "Overlay cancelled", controls.querySelector("[data-overlay-clear]")); });
    controls.hidden = false; controls.dataset.ready = "true";
  };
  const syncControls = () => {
    const controls = dashboard.querySelector("[data-live-controls]");
    if (!controls || !controls.dataset.ready) return;
    const current = {
      set_robot_state: (state.robot || {}).state,
      set_expression: (state.visual || {}).expression,
      set_visual_source: state.active_visual_source || (state.visual || {}).source,
    };
    for (const node of controls.querySelectorAll("[data-command]")) {
      // Preserve a pending user edit across status snapshots and SSE renders.
      // Once Apply succeeds the handler clears this flag and resynchronizes.
      if (node.dataset.dirty === "true") continue;
      node.querySelector("option[data-current-only]")?.remove();
      const value = current[node.dataset.command];
      if (!value) continue;
      if ([...node.options].some((option) => option.value === value)) {
        node.value = value;
      } else {
        // A read-only state (for example ERROR) must not visually become the
        // first writable option or trigger a command during initialization.
        const option = new Option(`${value} (current, read-only)`, value, true, true);
        option.disabled = true; option.dataset.currentOnly = "true";
        node.prepend(option); node.value = value;
      }
      if (node.dataset.command === "set_robot_state") {
        const transitions = ((capabilities.commands || {}).set_robot_state || {}).transitions || {};
        const allowed = new Set(transitions[value] || []);
        display("robot.transitions", allowed.size ? [...allowed].join(", ") : "No transition available");
        for (const option of node.options) {
          option.disabled = option.dataset.currentOnly === "true" ||
            (option.value !== value && !allowed.has(option.value));
        }
      }
    }
  };
  const commandRequest = async (path, payload, method = "POST", message = "Command applied", trigger) => {
    const feedback = trigger && trigger.closest(".control-card")?.querySelector(".control-feedback");
    const report = (text, kind) => {
      if (feedback) { feedback.textContent = text; feedback.className = `control-feedback ${kind}`; feedback.hidden = false; }
      else if (kind === "success" && success) { success.textContent = text; success.hidden = false; }
      else if (kind === "error" && error) { error.textContent = text; error.hidden = false; }
    };
    try { const response = await fetch(apiUrl(path), {method, credentials: "same-origin", headers: {"Content-Type": "application/json", Accept: "application/json"}, body: payload ? JSON.stringify(payload) : undefined}); const result = await readJson(response, `Command failed (${response.status})`); await snapshot(); report(message, "success"); return result; }
    catch (problem) { report(problem.message, "error"); return null; }
  };
  const applyEvent = (event) => {
    const payload = event.payload || {};
    if (event.type === "robot_state_changed") state.robot = {...(state.robot || {}), ...payload, state: payload.state || payload.current};
    else if (event.type === "expression_changed") state.visual = {...(state.visual || {}), ...payload};
    else if (event.type === "visual_state_changed") state.visual = {...(state.visual || {}), ...payload};
    else if (event.type === "environmental_state_changed") state.environment = payload;
    else if (event.type === "sensors_changed") state.sensors = mergeSensors(state.sensors, payload);
    else if (event.type === "motion_state_changed") state.motion = payload;
    else if (event.type === "health_changed") state.health = payload;
    else if (event.type === "overlay_changed" || event.type === "environmental_overlay_changed") state.overlay = payload;
    else if (event.type === "presence_changed") state.presence = payload;
    else if (event.type === "attention_changed") state.attention = payload;
    else if (event.type === "observed_expression_changed") state.observed_expression = payload;
    else if (event.type === "expression_reaction_changed") state.expression_reaction = payload;
    else if (event.type === "touch_changed") state.touch = payload;
    else if (event.type === "voice_changed") state.voice = payload;
    else if (event.type.startsWith("voice_")) state.voice = {...(state.voice || {}), ...(event.type === "voice_transcription_completed" ? {last_transcript: payload.text, last_confidence: payload.confidence, language: payload.language, state: "idle"} : {})};
    else if (["touch_tap", "touch_double_tap", "touch_long_press", "touch_swipe_left", "touch_swipe_right"].includes(event.type)) {
      state.touch = {...(state.touch || {}), last_event: payload.kind, x: payload.x, y: payload.y,
        normalized_x: payload.normalized_x, normalized_y: payload.normalized_y, duration_ms: payload.duration_ms,
        last_event_at: event.timestamp};
    }
    if (event.type === "expression_reaction_changed") console.debug("SSE IN:", event.type, payload);
    else if (["person_entered", "person_left", "attention_target_acquired", "attention_target_changed", "attention_target_lost"].includes(event.type)) {
      snapshot(); return;
    }
    else if (!["sensors_changed", "touch_changed", "touch_tap", "touch_double_tap", "touch_long_press", "touch_swipe_left", "touch_swipe_right", "voice_changed", "voice_listening_started", "voice_speech_started", "voice_speech_ended", "voice_transcription_started", "voice_transcription_completed", "voice_empty_utterance", "voice_session_cancelled", "voice_error"].includes(event.type)) return;
    render(); syncControls();
  };
  const fallback = () => { if (!fallbackTimer) fallbackTimer = window.setInterval(snapshot, 30000); };
  const connect = () => {
    window.clearTimeout(retryTimer); stream = new EventSource(endpoint + "/events", {withCredentials: true});
    stream.onopen = async () => { const reconnect = opened; opened = true; retries = 0; setConnection("connected"); window.clearInterval(fallbackTimer); fallbackTimer = undefined; if (reconnect) await snapshot(); };
    // The SSE adapter uses named semantic events, so listen explicitly rather
    // than relying on EventSource's unnamed-message default.
    for (const type of ["robot_state_changed", "expression_changed", "visual_state_changed", "environmental_state_changed", "sensors_changed", "environmental_overlay_changed", "motion_state_changed", "health_changed", "presence_changed", "attention_changed", "observed_expression_changed", "expression_reaction_changed", "touch_changed", "touch_tap", "touch_double_tap", "touch_long_press", "touch_swipe_left", "touch_swipe_right", "voice_changed", "voice_listening_started", "voice_speech_started", "voice_speech_ended", "voice_transcription_started", "voice_transcription_completed", "voice_empty_utterance", "voice_session_cancelled", "voice_error", "person_entered", "person_left", "attention_target_acquired", "attention_target_changed", "attention_target_lost", "overlay_changed"]) {
      stream.addEventListener(type, (message) => { try { applyEvent(JSON.parse(message.data)); } catch (_) {} });
    }
    stream.onerror = () => { stream.close(); setConnection("reconnecting"); fallback(); const delay = Math.min(30000, 1000 * 2 ** Math.min(retries++, 5)); retryTimer = window.setTimeout(connect, delay); };
  };
  if (!dashboard._phosFreshnessTimer) dashboard._phosFreshnessTimer = window.setInterval(renderFreshness, 1000);
  snapshot({refreshCapabilities: true}).then(connect); setConnection("reconnecting");
})();
