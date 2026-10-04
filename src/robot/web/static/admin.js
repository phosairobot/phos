"use strict";
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
  const age = (item) => item && Number.isFinite(item.age_seconds) ? `${item.age_seconds.toFixed(1)} seconds old` : "Unavailable";
  const reading = (item, field, suffix) => item && item.available && item.measurements && item.measurements[field] !== null ? `${item.measurements[field]} ${suffix}` : "Unavailable";
  const render = () => {
    const visual = state.visual || {};
    const environment = state.environment || {};
    const motion = state.motion || {};
    const ccs811 = state.ccs811 || (state.sensors || {}).ccs811 || {};
    const overlay = (state.overlay || {}).resolved || {};
    const presence = state.presence || {};
    const attention = state.attention || {};
    const target = attention.target || {};
    const observedExpression = state.observed_expression || {};
    const expressionReaction = state.expression_reaction || {};
    const touch = state.touch || {};
    display("robot.state", (state.robot || {}).state);
    display("visual.expression", visual.expression);
    display("visual.source", state.active_visual_source || visual.source);
    display("motion.motion_state", motion.motion_state || visual.motion_state);
    display("environment.state", environment.state || ((state.sensors || {}).environmental_behavior || {}).state);
    display("environment.status", status(environment)); display("environment.temperature", reading(environment, "temperature_c", "°C"));
    display("environment.pressure", reading(environment, "pressure_hpa", "hPa")); display("environment.age", age(environment));
    display("ccs811.status", status(ccs811)); display("ccs811.eco2", reading(ccs811, "eco2_ppm", "ppm"));
    display("ccs811.tvoc", reading(ccs811, "tvoc_ppb", "ppb")); display("ccs811.age", age(ccs811));
    display("motion.status", status(motion)); display("motion.age", age(motion));
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
      const apply = controls.querySelector(`[data-command-apply="${node.dataset.command}"]`);
      if (!apply) { node.disabled = true; continue; }
      if (!apply.dataset.bound) {
        apply.addEventListener("click", async () => { await commandRequest(command.endpoint, {[command.field]: node.value}, "POST", apply.textContent, apply); });
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
    try { const response = await fetch(apiUrl(path), {method, credentials: "same-origin", headers: {"Content-Type": "application/json", Accept: "application/json"}, body: payload ? JSON.stringify(payload) : undefined}); await readJson(response, `Command failed (${response.status})`); await snapshot(); report(message, "success"); }
    catch (problem) { report(problem.message, "error"); }
  };
  const applyEvent = (event) => {
    const payload = event.payload || {};
    if (event.type === "robot_state_changed") state.robot = {...(state.robot || {}), ...payload, state: payload.state || payload.current};
    else if (event.type === "expression_changed") state.visual = {...(state.visual || {}), ...payload};
    else if (event.type === "visual_state_changed") state.visual = {...(state.visual || {}), ...payload};
    else if (event.type === "environmental_state_changed") state.environment = payload;
    else if (event.type === "motion_state_changed") state.motion = payload;
    else if (event.type === "health_changed") state.health = payload;
    else if (event.type === "overlay_changed" || event.type === "environmental_overlay_changed") state.overlay = payload;
    else if (event.type === "presence_changed") state.presence = payload;
    else if (event.type === "attention_changed") state.attention = payload;
    else if (event.type === "observed_expression_changed") state.observed_expression = payload;
    else if (event.type === "expression_reaction_changed") state.expression_reaction = payload;
    else if (event.type === "touch_changed") state.touch = payload;
    else if (["touch_tap", "touch_long_press", "touch_swipe_left", "touch_swipe_right"].includes(event.type)) {
      state.touch = {...(state.touch || {}), last_event: payload.kind, x: payload.x, y: payload.y,
        normalized_x: payload.normalized_x, normalized_y: payload.normalized_y, duration_ms: payload.duration_ms,
        last_event_at: event.timestamp};
    }
    if (event.type === "expression_reaction_changed") console.debug("SSE IN:", event.type, payload);
    else if (["person_entered", "person_left", "attention_target_acquired", "attention_target_changed", "attention_target_lost"].includes(event.type)) {
      snapshot(); return;
    }
    else if (!["touch_changed", "touch_tap", "touch_long_press", "touch_swipe_left", "touch_swipe_right"].includes(event.type)) return;
    render(); syncControls();
  };
  const fallback = () => { if (!fallbackTimer) fallbackTimer = window.setInterval(snapshot, 30000); };
  const connect = () => {
    window.clearTimeout(retryTimer); stream = new EventSource(endpoint + "/events", {withCredentials: true});
    stream.onopen = async () => { const reconnect = opened; opened = true; retries = 0; setConnection("connected"); window.clearInterval(fallbackTimer); fallbackTimer = undefined; if (reconnect) await snapshot(); };
    // The SSE adapter uses named semantic events, so listen explicitly rather
    // than relying on EventSource's unnamed-message default.
    for (const type of ["robot_state_changed", "expression_changed", "visual_state_changed", "environmental_state_changed", "environmental_overlay_changed", "motion_state_changed", "health_changed", "presence_changed", "attention_changed", "observed_expression_changed", "expression_reaction_changed", "touch_changed", "touch_tap", "touch_long_press", "touch_swipe_left", "touch_swipe_right", "person_entered", "person_left", "attention_target_acquired", "attention_target_changed", "attention_target_lost", "overlay_changed"]) {
      stream.addEventListener(type, (message) => { try { applyEvent(JSON.parse(message.data)); } catch (_) {} });
    }
    stream.onerror = () => { stream.close(); setConnection("reconnecting"); fallback(); const delay = Math.min(30000, 1000 * 2 ** Math.min(retries++, 5)); retryTimer = window.setTimeout(connect, delay); };
  };
  snapshot({refreshCapabilities: true}).then(connect); setConnection("reconnecting");
})();
