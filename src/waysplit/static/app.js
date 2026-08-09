(function () {
  "use strict";

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const state = {
    csrfToken: csrfMeta ? csrfMeta.content : "",
    endpoints: [],
    selectedModel: null,
    probeInProgress: false,
    readiness: null,
    household: null,
    householdSaved: false,
    lockedParticipantIds: new Set(),
    splitwiseToken: null,
    splitwiseContext: null,
    statementFile: null,
    activeRun: null,
    runs: [],
    pollTimer: null,
    jsonEditorDirty: false,
    confirmationToken: null,
    rollbackConfirmationToken: null,
  };

  const refs = {};

  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  function byId(id) {
    return document.getElementById(id);
  }

  function node(tag, options, children) {
    const element = document.createElement(tag);
    const config = options || {};
    if (config.className) {
      element.className = config.className;
    }
    if (config.text !== undefined && config.text !== null) {
      element.textContent = String(config.text);
    }
    if (config.type) {
      element.type = config.type;
    }
    if (config.value !== undefined && config.value !== null) {
      element.value = String(config.value);
    }
    if (config.name) {
      element.name = config.name;
    }
    if (config.title) {
      element.title = config.title;
    }
    if (config.hidden) {
      element.hidden = true;
    }
    if (config.disabled) {
      element.disabled = true;
    }
    if (config.attrs) {
      Object.entries(config.attrs).forEach(([key, value]) => {
        if (value !== undefined && value !== null) {
          element.setAttribute(key, String(value));
        }
      });
    }
    if (config.dataset) {
      Object.entries(config.dataset).forEach(([key, value]) => {
        element.dataset[key] = String(value);
      });
    }
    if (config.on) {
      Object.entries(config.on).forEach(([eventName, handler]) => {
        element.addEventListener(eventName, handler);
      });
    }
    (children || []).forEach((child) => {
      if (child === null || child === undefined) {
        return;
      }
      element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    });
    return element;
  }

  function replace(element, children) {
    element.replaceChildren(...(children || []));
  }

  function setText(element, value) {
    element.textContent = value === null || value === undefined ? "" : String(value);
  }

  function normalizedError(payload, fallback) {
    if (!payload || typeof payload !== "object") {
      return fallback;
    }
    if (typeof payload.message === "string" && payload.message) {
      if (Array.isArray(payload.issues) && payload.issues.length) {
        const details = payload.issues.slice(0, 3).map((issue) => {
          const location = Array.isArray(issue.location) ? issue.location.slice(1).join(".") : "field";
          return `${location || "field"}: ${issue.message || "invalid"}`;
        });
        return `${payload.message} ${details.join(" · ")}`;
      }
      return payload.message;
    }
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    return fallback;
  }

  async function api(path, options) {
    const config = Object.assign({ method: "GET", credentials: "same-origin" }, options || {});
    const headers = new Headers(config.headers || {});
    if (config.method !== "GET" && config.method !== "HEAD") {
      headers.set("X-CSRF-Token", state.csrfToken);
    }
    if (config.body && !(config.body instanceof FormData) && typeof config.body !== "string") {
      headers.set("Content-Type", "application/json");
      config.body = JSON.stringify(config.body);
    }
    headers.set("Accept", "application/json");
    config.headers = headers;

    let response;
    try {
      response = await fetch(path, config);
    } catch (_error) {
      throw new ApiError("WaySplit could not reach its local service.", 0, null);
    }

    let payload = null;
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }
    }
    if (!response.ok) {
      throw new ApiError(
        normalizedError(payload, `The local service returned HTTP ${response.status}.`),
        response.status,
        payload,
      );
    }
    return payload;
  }

  function cacheReferences() {
    [
      "global-message",
      "audit-chip",
      "history-shortcut",
      "rescan-models",
      "model-discovery-state",
      "model-list",
      "model-empty",
      "probe-panel",
      "selected-model-name",
      "selected-model-meta",
      "probe-result",
      "probe-model",
      "household-form",
      "participant-list",
      "add-participant",
      "payer-select",
      "group-id",
      "output-destination",
      "save-household",
      "household-save-state",
      "splitwise-connect",
      "splitwise-connect-form",
      "connect-token",
      "connect-terms",
      "connect-splitwise",
      "splitwise-connect-state",
      "splitwise-connection-badge",
      "splitwise-context",
      "splitwise-current-user",
      "splitwise-group-choice",
      "splitwise-member-mapping",
      "apply-splitwise-context",
      "forget-splitwise",
      "upload-model-chip",
      "upload-form",
      "drop-zone",
      "statement-input",
      "selected-file",
      "start-run",
      "processing-strip",
      "processing-title",
      "processing-copy",
      "processing-run-id",
      "run-status-chip",
      "review-empty",
      "review-content",
      "statement-meta",
      "reconciliation-verdict",
      "whatsapp-summary",
      "whatsapp-summary-copy",
      "whatsapp-summary-text",
      "copy-whatsapp-summary",
      "balance-equation",
      "line-equation",
      "gate-panel",
      "charge-count",
      "charge-rows",
      "open-json-editor",
      "json-editor",
      "bill-json",
      "json-editor-state",
      "save-bill-json",
      "ownership-state",
      "build-preview",
      "preview-empty",
      "preview-content",
      "posting-outcome",
      "preview-description",
      "preview-cost",
      "preview-meta",
      "preview-digest",
      "preview-shares",
      "preview-blockers",
      "approval-title",
      "approval-copy",
      "open-confirmation",
      "refresh-history",
      "run-history",
      "history-count",
      "history-empty",
      "audit-card",
      "audit-title",
      "audit-copy",
      "audit-details",
      "verify-audit",
      "confirmation-dialog",
      "confirmation-form",
      "confirmation-summary",
      "ack-preview",
      "accept-terms",
      "splitwise-token",
      "confirmation-state",
      "confirm-post",
      "rollback-dialog",
      "rollback-form",
      "rollback-summary",
      "ack-rollback-target",
      "rollback-phrase",
      "rollback-token",
      "rollback-state",
      "confirm-rollback",
      "toast-region",
      "rail-trust-title",
      "rail-trust-copy",
    ].forEach((id) => {
      refs[id] = byId(id);
    });
  }

  function bindEvents() {
    document.querySelectorAll(".rail-step").forEach((button) => {
      button.addEventListener("click", () => scrollToSection(button.dataset.section));
    });
    refs["history-shortcut"].addEventListener("click", () => scrollToSection("history-section"));
    refs["rescan-models"].addEventListener("click", discoverModels);
    refs["probe-model"].addEventListener("click", probeSelectedModel);
    refs["add-participant"].addEventListener("click", addParticipant);
    refs["household-form"].addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveHousehold();
      } catch (_error) {
        // saveHousehold has already rendered actionable validation feedback.
      }
    });
    refs["household-form"].addEventListener("input", () => markHouseholdDirty());
    refs["household-form"].addEventListener("change", () => markHouseholdDirty());
    refs["payer-select"].addEventListener("change", (event) => {
      state.household.payer_participant_id = event.target.value || null;
    });
    refs["group-id"].addEventListener("input", (event) => {
      state.household.splitwise_group_id = event.target.value === "" ? null : Number(event.target.value);
    });
    refs["output-destination"].addEventListener("change", (event) => {
      state.household.output_destination = event.target.value;
      renderHousehold();
    });
    refs["connect-splitwise"].addEventListener("click", connectSplitwise);
    ["input", "change"].forEach((eventName) => {
      refs["splitwise-connect"].addEventListener(eventName, (event) => event.stopPropagation());
    });
    refs["connect-token"].addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        connectSplitwise();
      }
    });
    refs["splitwise-group-choice"].addEventListener("change", renderSplitwiseMemberMapping);
    refs["apply-splitwise-context"].addEventListener("click", applySplitwiseContext);
    refs["forget-splitwise"].addEventListener("click", forgetSplitwise);
    refs["statement-input"].addEventListener("change", () => {
      const files = refs["statement-input"].files;
      state.statementFile = files && files.length ? files[0] : null;
      renderSelectedFile();
    });
    ["dragenter", "dragover"].forEach((eventName) => {
      refs["drop-zone"].addEventListener(eventName, (event) => {
        event.preventDefault();
        refs["drop-zone"].classList.add("is-dragging");
      });
    });
    ["dragleave", "drop"].forEach((eventName) => {
      refs["drop-zone"].addEventListener(eventName, (event) => {
        event.preventDefault();
        refs["drop-zone"].classList.remove("is-dragging");
      });
    });
    refs["drop-zone"].addEventListener("drop", (event) => {
      const files = event.dataTransfer ? event.dataTransfer.files : null;
      if (files && files.length) {
        state.statementFile = files[0];
        renderSelectedFile();
      }
    });
    refs["upload-form"].addEventListener("submit", startRun);
    refs["open-json-editor"].addEventListener("click", openJsonEditor);
    refs["bill-json"].addEventListener("input", () => {
      state.jsonEditorDirty = true;
      setText(refs["json-editor-state"], "Unsaved JSON correction.");
    });
    refs["save-bill-json"].addEventListener("click", saveBillCorrection);
    refs["build-preview"].addEventListener("click", buildPreview);
    refs["copy-whatsapp-summary"].addEventListener("click", copyWhatsAppSummary);
    refs["open-confirmation"].addEventListener("click", openConfirmation);
    refs["confirmation-form"].addEventListener("submit", postConfirmedPreview);
    refs["rollback-form"].addEventListener("submit", rollbackPosting);
    [refs["rollback-phrase"], refs["ack-rollback-target"]].forEach((element) => {
      element.addEventListener("input", updateRollbackButton);
      element.addEventListener("change", updateRollbackButton);
    });
    refs["refresh-history"].addEventListener("click", refreshRecords);
    refs["verify-audit"].addEventListener("click", verifyAudit);
    document.querySelectorAll("[data-close-dialog]").forEach((button) => {
      button.addEventListener("click", () => closeDialog(byId(button.dataset.closeDialog)));
    });
    [refs["confirmation-dialog"], refs["rollback-dialog"]].forEach((dialog) => {
      dialog.addEventListener("close", () => clearDialogSecrets(dialog));
      dialog.addEventListener("click", (event) => {
        if (event.target === dialog) {
          closeDialog(dialog);
        }
      });
    });
    observeSections();
  }

  function observeSections() {
    if (!("IntersectionObserver" in window)) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((left, right) => right.intersectionRatio - left.intersectionRatio)[0];
        if (!visible) {
          return;
        }
        document.querySelectorAll(".rail-step").forEach((button) => {
          button.classList.toggle("is-active", button.dataset.section === visible.target.id);
        });
      },
      { rootMargin: "-15% 0px -68% 0px", threshold: [0, 0.12, 0.35] },
    );
    document.querySelectorAll(".workflow-section:not(.history-section)").forEach((section) => {
      observer.observe(section);
    });
  }

  function scrollToSection(id) {
    const section = byId(id);
    if (section) {
      section.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function busy(button, active, label) {
    if (active) {
      if (!button.dataset.restingLabel) {
        button.dataset.restingLabel = button.textContent;
      }
      button.disabled = true;
      setText(button, label || "Working…");
    } else {
      button.disabled = false;
      if (button.dataset.restingLabel) {
        setText(button, button.dataset.restingLabel);
      }
    }
  }

  function toast(message, kind) {
    const item = node("div", { className: `toast${kind === "error" ? " is-error" : ""}`, text: message });
    refs["toast-region"].append(item);
    window.setTimeout(() => item.remove(), 5200);
  }

  function showGlobal(message, kind) {
    refs["global-message"].hidden = !message;
    refs["global-message"].className = `global-message${kind === "error" ? " is-error" : ""}`;
    setText(refs["global-message"], message || "");
  }

  function formatBytes(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) {
      return "size unavailable";
    }
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    const units = ["KiB", "MiB", "GiB", "TiB"];
    let value = bytes / 1024;
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} ${units[index]}`;
  }

  function formatMoney(value, currency) {
    const text = String(value === undefined || value === null ? "" : value).trim();
    const match = /^([+-]?)(\d+)(?:\.(\d{1,2}))?$/.exec(text);
    if (!match) {
      return `${currency || "USD"} ${text || "—"}`;
    }
    const negative = match[1] === "-" && !/^0+$/.test(match[2]) ||
      (match[1] === "-" && !/^0{1,2}$/.test((match[3] || "").padEnd(2, "0")));
    const integer = match[2].replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const fraction = (match[3] || "").padEnd(2, "0");
    const code = currency || "USD";
    const symbols = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CAD: "CA$", AUD: "A$" };
    const amount = `${integer}.${fraction}`;
    return symbols[code]
      ? `${negative ? "-" : ""}${symbols[code]}${amount}`
      : `${negative ? "-" : ""}${code} ${amount}`;
  }

  function formatDate(value, includeTime) {
    if (!value) {
      return "—";
    }
    const dateOnly = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
    const date = dateOnly
      ? new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)))
      : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return new Intl.DateTimeFormat(undefined, includeTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" }).format(date);
  }

  function shortHash(value, length) {
    if (!value) {
      return "—";
    }
    const string = String(value);
    const desired = length || 12;
    return string.length > desired ? `${string.slice(0, desired)}…` : string;
  }

  function statusLabel(status) {
    const labels = {
      queued: "Queued",
      extracting: "Extracting locally",
      needs_review: "Needs review",
      blocked: "Blocked",
      ready: "Ready to confirm",
      submitting: "Posting",
      rollback_submitting: "Deleting from Splitwise",
      rollback_ambiguous: "Rollback outcome ambiguous",
      posted: "Posted & verified",
      posted_unverified: "Posted · unverified",
      ambiguous: "Ambiguous outcome",
      failed: "Failed",
      rolled_back: "Rolled back",
    };
    return labels[status] || String(status || "Unknown").replaceAll("_", " ");
  }

  function statusTone(status) {
    if (["posted", "ready", "rolled_back"].includes(status)) {
      return "good";
    }
    if (["blocked", "failed"].includes(status)) {
      return "bad";
    }
    if (["posted_unverified", "ambiguous", "rollback_ambiguous", "needs_review"].includes(status)) {
      return "warn";
    }
    return "idle";
  }

  async function loadHealth() {
    try {
      const health = await api("/api/health");
      if (health.local_only_default) {
        setText(refs["rail-trust-title"], "Local by default");
        setText(refs["rail-trust-copy"], "Remote model endpoints are disabled.");
      } else {
        setText(refs["rail-trust-title"], "Remote models allowed");
        setText(refs["rail-trust-copy"], "Check the selected endpoint before uploading.");
      }
    } catch (error) {
      setText(refs["rail-trust-title"], "Service unavailable");
      setText(refs["rail-trust-copy"], error.message);
    }
  }

  async function discoverModels() {
    busy(refs["rescan-models"], true, "Scanning…");
    refs["model-discovery-state"].hidden = false;
    refs["model-discovery-state"].className = "notice is-neutral";
    replace(refs["model-discovery-state"], [
      node("span", { className: "mini-spinner", attrs: { "aria-hidden": "true" } }),
      node("span", { text: "Looking for Ollama and OpenAI-compatible models…" }),
    ]);
    refs["model-empty"].hidden = true;
    replace(refs["model-list"], []);
    state.endpoints = [];
    state.selectedModel = null;
    state.readiness = null;
    renderModelSelection();
    updateWorkflow();
    try {
      const payload = await api("/api/models");
      state.endpoints = Array.isArray(payload.endpoints) ? payload.endpoints : [];
      refs["model-discovery-state"].hidden = true;
      renderModels();
    } catch (error) {
      refs["model-discovery-state"].className = "notice is-error";
      replace(refs["model-discovery-state"], [node("span", { text: error.message })]);
      refs["model-empty"].hidden = false;
    } finally {
      busy(refs["rescan-models"], false);
      updateWorkflow();
    }
  }

  function renderModels() {
    const items = [];
    let modelCount = 0;
    state.endpoints.forEach((endpoint) => {
      const endpointName = endpoint.endpoint || "Configured endpoint";
      items.push(node("div", {
        className: "endpoint-group",
        text: endpoint.error ? `${endpointName} · ${endpoint.error}` : endpointName,
      }));
      const models = Array.isArray(endpoint.models) ? endpoint.models : [];
      models.forEach((model) => {
        modelCount += 1;
        const selected = state.selectedModel && sameModel(state.selectedModel, model);
        const tags = [];
        if (model.provider) {
          tags.push(node("span", { className: "tag is-blue", text: String(model.provider).replaceAll("_", " ") }));
        }
        if (model.parameter_size) {
          tags.push(node("span", { className: "tag", text: model.parameter_size }));
        }
        if (model.quantization) {
          tags.push(node("span", { className: "tag", text: model.quantization }));
        }
        if (model.vision_hint) {
          tags.push(node("span", { className: "tag", text: "vision hint" }));
        }
        const details = node("div", {}, [
          node("strong", { text: model.name || "Unnamed model" }),
          node("p", { text: `${model.family || "local model"} · ${formatBytes(model.size_bytes)}` }),
          node("div", { className: "model-tags" }, tags),
        ]);
        const card = node("button", {
          className: `model-card${selected ? " is-selected" : ""}`,
          type: "button",
          attrs: {
            "aria-pressed": selected ? "true" : "false",
            "aria-label": `Select ${model.name || "local model"}`,
          },
          disabled: state.probeInProgress,
          on: { click: () => selectModel(model) },
        }, [
          node("span", { className: "model-radio", attrs: { "aria-hidden": "true" } }),
          details,
        ]);
        items.push(card);
      });
    });
    replace(refs["model-list"], items);
    refs["model-empty"].hidden = modelCount > 0;
  }

  function sameModel(left, right) {
    return Boolean(left && right && left.endpoint === right.endpoint && left.provider === right.provider && left.name === right.name);
  }

  function hasReadyAttestation() {
    return Boolean(
      state.readiness
      && state.readiness.ready
      && typeof state.readiness.attestation_token === "string"
      && state.readiness.attestation_token.length >= 20
    );
  }

  function selectModel(model) {
    if (state.probeInProgress) {
      return;
    }
    state.selectedModel = Object.assign({}, model);
    state.readiness = null;
    renderModels();
    renderModelSelection();
    updateWorkflow();
  }

  function renderModelSelection() {
    const model = state.selectedModel;
    refs["probe-panel"].hidden = !model;
    refs["probe-result"].className = "probe-result";
    if (!model) {
      setText(refs["selected-model-name"], "—");
      setText(refs["selected-model-meta"], "—");
      setText(refs["probe-result"], "");
      refs["upload-model-chip"].className = "model-chip is-locked";
      setText(refs["upload-model-chip"], "Choose a ready model first");
      refs["start-run"].disabled = true;
      return;
    }
    setText(refs["selected-model-name"], model.name);
    setText(refs["selected-model-meta"], `${String(model.provider).replaceAll("_", " ")} · ${model.endpoint}`);
    if (state.readiness) {
      refs["probe-result"].classList.add(state.readiness.ready ? "is-good" : "is-bad");
      const readinessBits = [state.readiness.reason];
      if (state.readiness.license_excerpt) {
        readinessBits.push(`License: ${state.readiness.license_excerpt}`);
      }
      setText(refs["probe-result"], readinessBits.filter(Boolean).join(" · "));
    } else {
      setText(refs["probe-result"], "A synthetic, statement-free extraction checks strict JSON and reconciliation.");
    }
    if (hasReadyAttestation()) {
      refs["upload-model-chip"].className = "model-chip is-ready";
      setText(refs["upload-model-chip"], `${model.name} · ready`);
    } else {
      refs["upload-model-chip"].className = "model-chip is-locked";
      setText(refs["upload-model-chip"], `${model.name} · readiness required`);
    }
    refs["start-run"].disabled = !(hasReadyAttestation() && state.statementFile);
  }

  async function probeSelectedModel() {
    if (!state.selectedModel) {
      return;
    }
    const requestedModel = Object.assign({}, state.selectedModel);
    state.probeInProgress = true;
    renderModels();
    busy(refs["probe-model"], true, "Testing synthetic bill…");
    refs["probe-result"].className = "probe-result";
    setText(refs["probe-result"], "No statement data is used in this test.");
    try {
      const readiness = await api("/api/models/probe", {
        method: "POST",
        body: {
          endpoint: requestedModel.endpoint,
          provider: requestedModel.provider,
          model: requestedModel.name,
        },
      });
      if (!sameModel(state.selectedModel, requestedModel)) {
        toast("The model selection changed; readiness result was discarded.", "error");
        return;
      }
      state.readiness = readiness;
      if (readiness.digest) {
        state.selectedModel.digest = readiness.digest;
      }
      renderModelSelection();
      const attested = readiness.ready && typeof readiness.attestation_token === "string";
      toast(attested ? "Local model is ready for statements." : "Model did not pass readiness.", attested ? "success" : "error");
    } catch (error) {
      if (sameModel(state.selectedModel, requestedModel)) {
        state.readiness = { ready: false, reason: error.message };
        renderModelSelection();
      }
    } finally {
      state.probeInProgress = false;
      busy(refs["probe-model"], false);
      renderModels();
      updateWorkflow();
    }
  }

  function blankHousehold() {
    return {
      participants: [{ id: "member-1", name: "", weight: "1", splitwise_user_id: null }],
      service_owners: {},
      payer_participant_id: null,
      splitwise_group_id: null,
      output_destination: "local_summary",
    };
  }

  async function loadHousehold() {
    try {
      const payload = await api("/api/household");
      state.household = payload.household || blankHousehold();
      state.household.participants = Array.isArray(state.household.participants)
        ? state.household.participants.map((participant) => Object.assign({}, participant, { weight: String(participant.weight) }))
        : blankHousehold().participants;
      state.household.service_owners = state.household.service_owners || {};
      state.householdSaved = Boolean(payload.household);
      state.lockedParticipantIds = new Set(payload.household
        ? state.household.participants.map((participant) => participant.id)
        : []);
    } catch (error) {
      state.household = blankHousehold();
      state.householdSaved = false;
      showGlobal(`Household settings could not be loaded: ${error.message}`, "error");
    }
    renderHousehold();
    updateWorkflow();
  }

  function renderHousehold() {
    if (!state.household) {
      state.household = blankHousehold();
    }
    const rows = state.household.participants.map((participant, index) => participantRow(participant, index));
    replace(refs["participant-list"], rows);
    renderPayerOptions();
    refs["group-id"].value = state.household.splitwise_group_id === null || state.household.splitwise_group_id === undefined
      ? ""
      : String(state.household.splitwise_group_id);
    refs["output-destination"].value = state.household.output_destination || "local_summary";
    refs["household-save-state"].className = `save-state${state.householdSaved ? " is-saved" : " is-dirty"}`;
    setText(refs["household-save-state"], state.householdSaved ? "Saved locally" : "Unsaved changes");
    if (state.splitwiseContext) {
      renderSplitwiseContext();
    }
  }

  function participantRow(participant, index) {
    const nameInput = node("input", {
      type: "text",
      value: participant.name,
      attrs: { maxlength: "80", required: "", placeholder: "e.g. Dennis" },
      on: {
        input: (event) => {
          state.household.participants[index].name = event.target.value;
          updatePayerOptionLabels();
        },
      },
    });
    const idLocked = state.lockedParticipantIds.has(participant.id);
    const idInput = node("input", {
      className: "id-input",
      type: "text",
      value: participant.id,
      attrs: {
        maxlength: "64",
        pattern: "[a-z0-9][a-z0-9_-]{0,63}",
        required: "",
        readonly: idLocked ? "" : null,
        "aria-describedby": `participant-id-help-${index}`,
      },
      on: {
        input: (event) => updateParticipantId(index, event.target.value),
      },
    });
    const weightInput = node("input", {
      className: "weight-input",
      type: "number",
      value: participant.weight,
      attrs: { min: "0.01", step: "0.01", inputmode: "decimal", required: "" },
      on: { input: (event) => { state.household.participants[index].weight = event.target.value; } },
    });
    const splitwiseInput = node("input", {
      className: "splitwise-input",
      type: "number",
      value: participant.splitwise_user_id === null || participant.splitwise_user_id === undefined ? "" : participant.splitwise_user_id,
      attrs: { min: "1", step: "1", inputmode: "numeric", placeholder: "Optional until preview" },
      on: {
        input: (event) => {
          state.household.participants[index].splitwise_user_id = event.target.value || null;
        },
      },
    });
    const removeButton = node("button", {
      className: "remove-participant",
      type: "button",
      text: "×",
      disabled: state.household.participants.length <= 1,
      attrs: { "aria-label": `Remove ${participant.name || participant.id || "participant"}` },
      on: { click: () => removeParticipant(index) },
    });
    return node("div", { className: "participant-row", dataset: { participantIndex: index } }, [
      field("Name", nameInput),
      field("Stable ID", idInput, idLocked ? "Locked after first save." : "Lowercase slug; locks after save.", `participant-id-help-${index}`),
      field("Weight", weightInput),
      field("Splitwise user ID", splitwiseInput, null, null, "splitwise-field"),
      removeButton,
    ]);
  }

  function field(label, control, help, helpId, extraClass) {
    const children = [node("span", { text: label }), control];
    if (help) {
      children.push(node("small", { text: help, attrs: helpId ? { id: helpId } : {} }));
    }
    return node("label", { className: `field${extraClass ? ` ${extraClass}` : ""}` }, children);
  }

  function updateParticipantId(index, newId) {
    const participant = state.household.participants[index];
    const oldId = participant.id;
    participant.id = newId;
    if (state.household.payer_participant_id === oldId) {
      state.household.payer_participant_id = newId;
    }
    Object.keys(state.household.service_owners).forEach((service) => {
      if (state.household.service_owners[service] === oldId) {
        state.household.service_owners[service] = newId;
      }
    });
    renderPayerOptions();
    renderCharges();
  }

  function nextParticipantId() {
    const used = new Set(state.household.participants.map((participant) => participant.id));
    let index = state.household.participants.length + 1;
    while (used.has(`member-${index}`)) {
      index += 1;
    }
    return `member-${index}`;
  }

  function addParticipant() {
    state.household.participants.push({
      id: nextParticipantId(),
      name: "",
      weight: "1",
      splitwise_user_id: null,
    });
    state.householdSaved = false;
    renderHousehold();
    renderCharges();
    const inputs = refs["participant-list"].querySelectorAll('input[type="text"]');
    if (inputs.length) {
      inputs[inputs.length - 2].focus();
    }
    updateWorkflow();
  }

  function removeParticipant(index) {
    if (state.household.participants.length <= 1) {
      return;
    }
    const removed = state.household.participants.splice(index, 1)[0];
    if (state.household.payer_participant_id === removed.id) {
      state.household.payer_participant_id = null;
    }
    Object.keys(state.household.service_owners).forEach((service) => {
      if (state.household.service_owners[service] === removed.id) {
        delete state.household.service_owners[service];
      }
    });
    state.householdSaved = false;
    renderHousehold();
    renderCharges();
    updateWorkflow();
  }

  function renderPayerOptions() {
    const current = state.household.payer_participant_id || "";
    const options = [node("option", { value: "", text: "Choose a payer" })];
    state.household.participants.forEach((participant) => {
      const option = node("option", {
        value: participant.id,
        text: participant.name || participant.id || "Unnamed participant",
      });
      if (participant.id === current) {
        option.selected = true;
      }
      options.push(option);
    });
    replace(refs["payer-select"], options);
    refs["payer-select"].value = current;
  }

  function updatePayerOptionLabels() {
    const options = Array.from(refs["payer-select"].options).slice(1);
    options.forEach((option, index) => {
      const participant = state.household.participants[index];
      if (participant) {
        setText(option, participant.name || participant.id || "Unnamed participant");
      }
    });
  }

  function markHouseholdDirty() {
    if (!state.household) {
      return;
    }
    state.householdSaved = false;
    refs["household-save-state"].className = "save-state is-dirty";
    setText(refs["household-save-state"], "Unsaved changes");
    updateWorkflow();
  }

  async function connectSplitwise() {
    const token = refs["connect-token"].value;
    if (!refs["connect-terms"].checked) {
      refs["splitwise-connect-state"].className = "dialog-state is-error";
      setText(refs["splitwise-connect-state"], "Accept the Splitwise API terms and privacy boundary before connecting.");
      return;
    }
    busy(refs["connect-splitwise"], true, "Loading account…");
    refs["splitwise-connect-state"].className = "dialog-state";
    setText(refs["splitwise-connect-state"], "Requesting group and member IDs from Splitwise…");
    refs["connect-token"].value = "";
    try {
      const payload = await api("/api/splitwise/context", {
        method: "POST",
        body: { access_token: token || null, accepted_destination_terms: true },
      });
      state.splitwiseToken = token || null;
      state.splitwiseContext = payload;
      refs["connect-terms"].checked = false;
      refs["splitwise-connect-state"].className = "dialog-state difference-pass";
      setText(refs["splitwise-connect-state"], "Account choices loaded. The token exists only in this tab's memory.");
      renderSplitwiseContext();
      toast("Splitwise account connected for this browser tab.");
    } catch (error) {
      state.splitwiseToken = null;
      state.splitwiseContext = null;
      refs["connect-token"].value = "";
      refs["splitwise-connect-state"].className = "dialog-state is-error";
      setText(refs["splitwise-connect-state"], error.message);
      renderSplitwiseContext();
    } finally {
      busy(refs["connect-splitwise"], false);
    }
  }

  function renderSplitwiseContext() {
    const context = state.splitwiseContext;
    const connected = Boolean(context);
    refs["splitwise-context"].hidden = !connected;
    refs["splitwise-connection-badge"].className = `save-state${connected ? " is-saved" : ""}`;
    setText(refs["splitwise-connection-badge"], connected ? "Connected in memory" : "Not connected");
    if (!connected) {
      replace(refs["splitwise-group-choice"], []);
      replace(refs["splitwise-member-mapping"], []);
      return;
    }
    const currentUser = context.current_user || {};
    setText(refs["splitwise-current-user"], `${currentUser.display_name || "Splitwise user"} · ID ${currentUser.user_id || "—"}`);
    const groups = Array.isArray(context.groups) ? context.groups : [];
    const choices = [node("option", { value: "", text: "Choose a Splitwise group" })];
    groups.forEach((group) => {
      const option = node("option", {
        value: group.group_id,
        text: `${group.name || "Unnamed group"} · ${group.members ? group.members.length : 0} members`,
      });
      if (state.household && String(state.household.splitwise_group_id) === String(group.group_id)) {
        option.selected = true;
      }
      choices.push(option);
    });
    replace(refs["splitwise-group-choice"], choices);
    if (!refs["splitwise-group-choice"].value && groups.length === 1) {
      refs["splitwise-group-choice"].value = String(groups[0].group_id);
    }
    renderSplitwiseMemberMapping();
  }

  function selectedSplitwiseGroup() {
    if (!state.splitwiseContext) {
      return null;
    }
    const groupId = refs["splitwise-group-choice"].value;
    return (state.splitwiseContext.groups || []).find((group) => String(group.group_id) === groupId) || null;
  }

  function renderSplitwiseMemberMapping() {
    const group = selectedSplitwiseGroup();
    const participants = state.household ? state.household.participants : [];
    if (!group) {
      replace(refs["splitwise-member-mapping"], [
        node("div", { className: "notice is-neutral", text: "Choose a group to map household participants." }),
      ]);
      refs["apply-splitwise-context"].disabled = true;
      return;
    }
    const members = Array.isArray(group.members) ? group.members : [];
    const rows = participants.map((participant, index) => {
      const select = node("select", {
        dataset: { participantIndex: index },
        attrs: { "aria-label": `Splitwise member for ${participant.name || participant.id}` },
      });
      select.append(node("option", { value: "", text: "Keep manual ID / not in group" }));
      const exactName = String(participant.name || "").trim().toLocaleLowerCase();
      members.forEach((member) => {
        const option = node("option", {
          value: member.user_id,
          text: `${member.display_name || "Unnamed member"} · ID ${member.user_id}`,
        });
        const sameExistingId = participant.splitwise_user_id && String(participant.splitwise_user_id) === String(member.user_id);
        const exactMatch = exactName && exactName === String(member.display_name || "").trim().toLocaleLowerCase();
        option.selected = Boolean(sameExistingId || (!participant.splitwise_user_id && exactMatch));
        select.append(option);
      });
      return node("div", { className: "member-map-row" }, [
        node("div", {}, [
          node("strong", { text: participant.name || "Unnamed participant" }),
          node("small", { text: participant.id }),
        ]),
        select,
      ]);
    });
    replace(refs["splitwise-member-mapping"], rows);
    refs["apply-splitwise-context"].disabled = false;
  }

  function applySplitwiseContext() {
    const group = selectedSplitwiseGroup();
    if (!group || !state.household) {
      return;
    }
    state.household.splitwise_group_id = Number(group.group_id);
    refs["group-id"].value = String(group.group_id);
    refs["splitwise-member-mapping"].querySelectorAll("select").forEach((select) => {
      const index = Number(select.dataset.participantIndex);
      const participant = state.household.participants[index];
      if (participant && select.value) {
        participant.splitwise_user_id = Number(select.value);
      }
    });
    state.householdSaved = false;
    renderHousehold();
    refs["splitwise-connect"].open = true;
    renderSplitwiseContext();
    markHouseholdDirty();
    toast("Selected Splitwise IDs filled into the household. Review and save them.");
  }

  function forgetSplitwise() {
    state.splitwiseToken = null;
    state.splitwiseContext = null;
    refs["connect-token"].value = "";
    refs["connect-terms"].checked = false;
    refs["splitwise-connect-state"].className = "dialog-state";
    setText(refs["splitwise-connect-state"], "Token forgotten. Saved numeric IDs remain in the household form.");
    renderSplitwiseContext();
    toast("In-memory Splitwise token forgotten.");
  }

  function collectHousehold() {
    const participants = state.household.participants.map((participant, index) => {
      const row = refs["participant-list"].querySelector(`[data-participant-index="${index}"]`);
      const inputs = row ? row.querySelectorAll("input") : [];
      const name = inputs[0] ? inputs[0].value.trim() : String(participant.name || "").trim();
      const id = inputs[1] ? inputs[1].value.trim() : String(participant.id || "").trim();
      const weight = inputs[2] ? inputs[2].value.trim() : String(participant.weight || "").trim();
      const splitwiseRaw = inputs[3] ? inputs[3].value.trim() : "";
      if (!name) {
        throw new Error(`Participant ${index + 1} needs a name.`);
      }
      if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(id)) {
        throw new Error(`${name}'s stable ID must be a lowercase slug using letters, numbers, _ or -.`);
      }
      if (!/^(?:\d+)(?:\.\d+)?$/.test(weight) || Number(weight) <= 0) {
        throw new Error(`${name}'s weight must be a decimal greater than zero.`);
      }
      if (splitwiseRaw && (!/^\d+$/.test(splitwiseRaw) || Number(splitwiseRaw) <= 0)) {
        throw new Error(`${name}'s Splitwise user ID must be a positive whole number.`);
      }
      return {
        id,
        name,
        weight,
        splitwise_user_id: splitwiseRaw ? Number(splitwiseRaw) : null,
      };
    });
    const participantIds = participants.map((participant) => participant.id);
    if (new Set(participantIds).size !== participantIds.length) {
      throw new Error("Every participant needs a unique stable ID.");
    }
    const known = new Set(participantIds);
    const serviceOwners = {};
    Object.entries(state.household.service_owners || {}).forEach(([service, owner]) => {
      if (service.trim() && known.has(owner)) {
        serviceOwners[service] = owner;
      }
    });
    document.querySelectorAll(".owner-select").forEach((select) => {
      if (select.dataset.serviceKey && select.value && known.has(select.value)) {
        serviceOwners[select.dataset.serviceKey] = select.value;
      }
    });
    const payer = refs["payer-select"].value || null;
    const groupRaw = refs["group-id"].value.trim();
    if (groupRaw && (!/^\d+$/.test(groupRaw) || Number(groupRaw) < 0)) {
      throw new Error("Splitwise group ID must be a non-negative whole number.");
    }
    return {
      participants,
      service_owners: serviceOwners,
      payer_participant_id: payer,
      splitwise_group_id: groupRaw ? Number(groupRaw) : null,
      output_destination: refs["output-destination"].value === "splitwise" ? "splitwise" : "local_summary",
    };
  }

  async function saveHousehold(options) {
    const config = options || {};
    let household;
    try {
      household = collectHousehold();
    } catch (error) {
      toast(error.message, "error");
      throw error;
    }
    busy(refs["save-household"], true, "Saving…");
    try {
      const payload = await api("/api/household", { method: "PUT", body: household });
      state.household = payload.household;
      state.household.participants = state.household.participants.map((participant) => Object.assign({}, participant, { weight: String(participant.weight) }));
      state.householdSaved = true;
      state.lockedParticipantIds = new Set(state.household.participants.map((participant) => participant.id));
      renderHousehold();
      renderCharges();
      if (!config.quiet) {
        toast("Household rules saved locally.");
      }
      updateWorkflow();
      return state.household;
    } catch (error) {
      state.householdSaved = false;
      toast(error.message, "error");
      throw error;
    } finally {
      busy(refs["save-household"], false);
    }
  }

  function renderSelectedFile() {
    if (state.statementFile) {
      setText(refs["selected-file"], `${state.statementFile.name} · ${formatBytes(state.statementFile.size)}`);
    } else {
      setText(refs["selected-file"], "No file selected");
    }
    refs["start-run"].disabled = !(state.statementFile && hasReadyAttestation());
  }

  async function startRun(event) {
    event.preventDefault();
    showGlobal("");
    if (!state.selectedModel || !hasReadyAttestation()) {
      toast("Run the selected model's readiness test first.", "error");
      scrollToSection("models-section");
      return;
    }
    if (!state.statementFile) {
      toast("Choose a PDF or image statement first.", "error");
      return;
    }
    const formData = new FormData();
    formData.append("statement", state.statementFile, state.statementFile.name);
    formData.append("endpoint", state.selectedModel.endpoint);
    formData.append("provider", state.selectedModel.provider);
    formData.append("model", state.selectedModel.name);
    formData.append("probe_attestation", state.readiness.attestation_token);
    busy(refs["start-run"], true, "Starting local extraction…");
    try {
      const payload = await api("/api/runs", { method: "POST", body: formData });
      state.activeRun = payload.run;
      state.jsonEditorDirty = false;
      state.statementFile = null;
      refs["statement-input"].value = "";
      renderSelectedFile();
      renderActiveRun();
      scrollToSection("review-section");
      schedulePoll(350);
      await loadRuns();
      toast("Statement accepted for local extraction.");
    } catch (error) {
      if (error.payload && error.payload.error === "duplicate_statement" && error.payload.existing_run_id) {
        toast("This source file was already processed. Opening its original run.", "error");
        await loadRun(error.payload.existing_run_id);
        scrollToSection("review-section");
      } else {
        showGlobal(error.message, "error");
      }
    } finally {
      busy(refs["start-run"], false);
      renderSelectedFile();
    }
  }

  function isPollingStatus(status) {
    return ["queued", "extracting", "submitting", "rollback_submitting"].includes(status);
  }

  function schedulePoll(delay) {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
    }
    if (!state.activeRun || !isPollingStatus(state.activeRun.status)) {
      state.pollTimer = null;
      return;
    }
    const runId = state.activeRun.id;
    state.pollTimer = window.setTimeout(async () => {
      try {
        const payload = await api(`/api/runs/${encodeURIComponent(runId)}`);
        if (!state.activeRun || state.activeRun.id !== runId) {
          return;
        }
        state.activeRun = payload.run;
        renderActiveRun();
        if (isPollingStatus(state.activeRun.status)) {
          schedulePoll(1400);
        } else {
          await refreshRecords();
          if (state.activeRun.bill) {
            toast(state.activeRun.status === "blocked"
              ? "Extraction finished, but safety gates need attention."
              : "Extraction finished. Review every line before previewing.",
            state.activeRun.status === "blocked" ? "error" : "success");
          }
        }
      } catch (error) {
        showGlobal(`Run polling paused: ${error.message}`, "error");
        schedulePoll(3000);
      }
    }, delay || 1200);
  }

  async function loadRun(runId) {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = null;
    }
    try {
      const payload = await api(`/api/runs/${encodeURIComponent(runId)}`);
      state.activeRun = payload.run;
      state.jsonEditorDirty = false;
      renderActiveRun();
      renderHistory();
      if (isPollingStatus(state.activeRun.status)) {
        schedulePoll(600);
      }
    } catch (error) {
      showGlobal(`Run could not be opened: ${error.message}`, "error");
    }
  }

  function renderActiveRun() {
    const run = state.activeRun;
    renderProcessing(run);
    renderRunStatus(run);
    if (!run || !run.bill) {
      refs["review-empty"].hidden = false;
      refs["review-content"].hidden = true;
      const heading = refs["review-empty"].querySelector("h3");
      const copy = refs["review-empty"].querySelector("p");
      if (run && run.status === "failed") {
        setText(heading, "Extraction stopped safely");
        setText(copy, run.error ? run.error.message : "Review the local logs and try a compatible model.");
      } else if (run && isPollingStatus(run.status)) {
        setText(heading, "Local extraction is in progress");
        setText(copy, "WaySplit will show the normalized ledger as soon as deterministic checks finish.");
      } else {
        setText(heading, "No extracted statement yet");
        setText(copy, "Select a ready local model and upload a statement to begin.");
      }
      renderWhatsAppSummary();
      renderPreview();
      updateWorkflow();
      return;
    }
    refs["review-empty"].hidden = true;
    refs["review-content"].hidden = false;
    renderStatementMeta();
    renderReconciliation();
    renderGate();
    renderCharges();
    const billEditable = ["blocked", "needs_review", "ready", "failed"].includes(run.status);
    refs["open-json-editor"].disabled = !billEditable;
    refs["save-bill-json"].disabled = !billEditable;
    refs["open-json-editor"].title = billEditable
      ? "Correct verified extraction facts"
      : "Posted, ambiguous, and rolled-back runs are immutable";
    if (!billEditable) {
      refs["json-editor"].open = false;
    }
    if (!state.jsonEditorDirty) {
      refs["bill-json"].value = JSON.stringify(run.bill, null, 2);
      setText(refs["json-editor-state"], "Strict schema validation applies.");
    }
    renderWhatsAppSummary();
    renderPreview();
    updateWorkflow();
  }

  function renderProcessing(run) {
    const processing = run && isPollingStatus(run.status);
    refs["processing-strip"].hidden = !processing;
    if (!processing) {
      return;
    }
    if (run.status === "queued") {
      setText(refs["processing-title"], "Queued on this machine");
      setText(refs["processing-copy"], "The local extraction worker will start shortly.");
    } else if (run.status === "extracting") {
      setText(refs["processing-title"], "Reading and reconciling locally");
      setText(refs["processing-copy"], "Extracting text, requesting strict bill JSON, then checking every cent.");
    } else {
      setText(refs["processing-title"], "Waiting for Splitwise");
      setText(refs["processing-copy"], "Do not retry while the destination outcome is unknown.");
    }
    setText(refs["processing-run-id"], shortHash(run.id, 16));
  }

  function renderRunStatus(run) {
    const tone = run ? statusTone(run.status) : "idle";
    refs["run-status-chip"].className = `status-chip is-${tone}`;
    replace(refs["run-status-chip"], [
      node("span", { className: "status-dot", attrs: { "aria-hidden": "true" } }),
      node("span", { text: run ? statusLabel(run.status) : "No active run" }),
    ]);
  }

  function renderStatementMeta() {
    const run = state.activeRun;
    const bill = run.bill;
    const period = bill.statement.period_start && bill.statement.period_end
      ? `${formatDate(bill.statement.period_start)} – ${formatDate(bill.statement.period_end)}`
      : "Not extracted";
    const values = [
      ["Statement", run.source_name || "Local statement"],
      ["Issuer", bill.issuer.name],
      ["Period", period],
      ["Model", run.model ? run.model.name : "—"],
    ];
    replace(refs["statement-meta"], values.map(([label, value]) => {
      const dl = node("dl", { className: "meta-cell" });
      dl.append(node("dt", { text: label }), node("dd", { text: value, title: value }));
      return dl;
    }));
  }

  function equationValue(label, value, currency) {
    return node("div", { className: "equation-value" }, [
      node("span", { text: label }),
      node("strong", { text: formatMoney(value, currency) }),
    ]);
  }

  function renderReconciliation() {
    const run = state.activeRun;
    const bill = run.bill;
    const totals = bill.totals;
    const currency = bill.account.currency;
    const reconciliation = run.reconciliation || {};
    const balanceCheck = reconciliation.balance_equation || {};
    const lineCheck = reconciliation.line_items || {};
    const passed = Boolean(reconciliation.reconciled);
    refs["reconciliation-verdict"].className = `verdict${passed ? "" : " is-fail"}`;
    setText(refs["reconciliation-verdict"], passed ? "Both checks pass" : "Posting blocked");
    replace(refs["balance-equation"], [
      equationValue("Balance forward", totals.balance_forward, currency),
      node("span", { className: "equation-operator", text: "+", attrs: { "aria-hidden": "true" } }),
      equationValue("Payments / credits", totals.payments_and_credits, currency),
      node("span", { className: "equation-operator", text: "+", attrs: { "aria-hidden": "true" } }),
      equationValue("Current charges", totals.current_charges, currency),
      node("span", { className: "equation-operator", text: "+", attrs: { "aria-hidden": "true" } }),
      equationValue("Adjustments", totals.other_adjustments, currency),
      node("span", { className: "equation-operator", text: "=", attrs: { "aria-hidden": "true" } }),
      equationValue("Amount due", totals.amount_due, currency),
    ]);
    const differenceClass = lineCheck.passed ? "difference-pass" : "difference-fail";
    replace(refs["line-equation"], [
      node("span", {}, [
        "Σ ",
        node("strong", { text: `${bill.charges.length} extracted charges` }),
        ` ${formatMoney(lineCheck.actual, currency)} = reported current charges ${formatMoney(lineCheck.expected, currency)}`,
      ]),
      node("strong", {
        className: differenceClass,
        text: `difference ${formatMoney(lineCheck.difference, currency)}`,
      }),
    ]);
    if (!balanceCheck.passed && balanceCheck.difference !== undefined) {
      refs["reconciliation-verdict"].title = `Balance equation difference: ${formatMoney(balanceCheck.difference, currency)}`;
    } else {
      refs["reconciliation-verdict"].removeAttribute("title");
    }
  }

  function renderWhatsAppSummary() {
    const run = state.activeRun;
    const panel = refs["whatsapp-summary"];
    if (!run || !run.bill) {
      panel.hidden = true;
      return;
    }
    const preview = run.preview;
    if (!preview || !Array.isArray(preview.shares) || !preview.shares.length) {
      panel.hidden = true;
      refs["whatsapp-summary-text"].value = "";
      refs["copy-whatsapp-summary"].disabled = true;
      return;
    }
    panel.hidden = false;
    const bill = run.bill;
    const statement = bill.statement || {};
    const currency = preview.currency_code || bill.account?.currency || "USD";
    const period = statement.period_start && statement.period_end
      ? `${formatDate(statement.period_start)} – ${formatDate(statement.period_end)}`
      : `Issued ${formatDate(statement.issued_on)}`;
    const lines = [
      "📱 Mobile bill split",
      `Billing cycle: ${period}`,
      `Total bill: ${formatMoney(preview.cost, currency)}`,
      "",
      "Breakdown:",
      ...preview.shares.map((share) => `${share.participant_name || share.participant_id}: ${formatMoney(share.owed_share, currency)}`),
      "",
      "Please check your amount against the statement.",
    ];
    refs["whatsapp-summary-text"].value = lines.join("\n");
    refs["copy-whatsapp-summary"].disabled = false;
    setText(refs["whatsapp-summary-copy"], "Generated from the reviewed deterministic preview. It is not sent automatically.");
  }

  async function copyWhatsAppSummary() {
    const value = refs["whatsapp-summary-text"].value;
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
    } catch (_error) {
      refs["whatsapp-summary-text"].focus();
      refs["whatsapp-summary-text"].select();
      document.execCommand("copy");
    }
    toast("WhatsApp message copied.", "success");
  }

  function renderGate() {
    const gate = state.activeRun.gate || {};
    const blocked = state.activeRun.status === "blocked" || gate.status === "blocked";
    const reasons = [];
    (Array.isArray(gate.reasons) ? gate.reasons : []).forEach((reason) => {
      if (reason.code !== "explicit_confirmation_required") {
        const ids = Array.isArray(reason.charge_ids) && reason.charge_ids.length
          ? ` (${reason.charge_ids.join(", ")})`
          : "";
        reasons.push(`${reason.message || reason.code}${ids}`);
      }
    });
    (Array.isArray(gate.destination_blockers) ? gate.destination_blockers : []).forEach((reason) => reasons.push(reason));
    const uniqueReasons = Array.from(new Set(reasons));
    const summary = node("div", { className: `gate-summary${blocked ? " is-blocked" : ""}` }, [
      node("span", { className: "gate-icon", text: blocked ? "!" : "✓", attrs: { "aria-hidden": "true" } }),
      node("div", {}, [
        node("strong", { text: blocked ? "A safety gate is blocking external posting" : "Extraction gates are clear" }),
        node("p", {
          text: blocked
            ? "Correct the verified bill facts or destination settings, then rebuild the preview."
            : "An explicit preview acknowledgement is still required before any destination call.",
        }),
      ]),
    ]);
    if (uniqueReasons.length) {
      const list = node("ul", { className: "gate-reasons" });
      uniqueReasons.forEach((reason) => list.append(node("li", { text: reason })));
      summary.lastElementChild.append(list);
    }
    replace(refs["gate-panel"], [summary]);
  }

  function renderCharges() {
    const run = state.activeRun;
    if (!run || !run.bill) {
      replace(refs["charge-rows"], []);
      return;
    }
    const bill = run.bill;
    const currency = bill.account.currency;
    const participants = state.household && Array.isArray(state.household.participants)
      ? state.household.participants
      : [];
    setText(refs["charge-count"], `${bill.charges.length} line item${bill.charges.length === 1 ? "" : "s"} · ${currency}`);
    const rows = bill.charges.map((charge) => {
      const chargeName = node("div", { className: "charge-name" }, [
        node("strong", { text: charge.description }),
        node("small", { text: charge.service_identifier || charge.charge_id }),
      ]);
      const type = node("div", { className: "model-tags" }, [
        node("span", { className: "tag", text: charge.category || "other" }),
        node("span", { className: charge.scope === "line" ? "tag is-blue" : "tag", text: charge.scope || "account" }),
      ]);
      const evidence = renderEvidence(charge.evidence);
      const confidenceNumber = Math.max(0, Math.min(1, Number(charge.confidence)));
      const confidence = node("div", { className: `confidence${confidenceNumber < 0.8 ? " is-low" : ""}` }, [
        node("meter", {
          className: "confidence-meter",
          value: confidenceNumber,
          attrs: { min: "0", max: "1", "aria-label": `Confidence ${Math.round(confidenceNumber * 100)} percent` },
        }),
        node("code", { text: `${Math.round(confidenceNumber * 100)}%` }),
      ]);
      const owner = renderOwnerControl(charge, participants);
      return node("tr", {}, [
        node("td", {}, [chargeName]),
        node("td", {}, [type]),
        node("td", {}, [evidence]),
        node("td", {}, [confidence]),
        node("td", {}, [owner]),
        node("td", { className: "number-cell", text: formatMoney(charge.amount, currency) }),
      ]);
    });
    replace(refs["charge-rows"], rows);
    updateOwnershipState();
  }

  function renderEvidence(evidenceValue) {
    const evidence = Array.isArray(evidenceValue) ? evidenceValue : [];
    if (!evidence.length) {
      return node("span", { className: "tag", text: "missing" });
    }
    const details = node("details", { className: "evidence-details" });
    details.append(node("summary", { text: `${evidence.length} source${evidence.length === 1 ? "" : "s"}` }));
    const list = node("ul");
    evidence.forEach((item) => {
      const location = item.page ? ` · page ${item.page}` : "";
      list.append(node("li", { text: `${item.source || "source"}${location}: ${item.text || "No excerpt"}` }));
    });
    details.append(list);
    return details;
  }

  function renderOwnerControl(charge, participants) {
    if (charge.scope !== "line") {
      return node("span", { className: "shared-rule", text: "Shared by weight" });
    }
    if (!charge.service_identifier) {
      return node("span", { className: "tag", text: "Fix service ID in JSON" });
    }
    const selectedOwner = state.household && state.household.service_owners
      ? state.household.service_owners[charge.service_identifier]
      : null;
    const select = node("select", {
      className: "owner-select",
      dataset: { serviceKey: charge.service_identifier },
      disabled: participants.length === 0,
      attrs: { "aria-label": `Owner for ${charge.description}` },
      on: {
        change: (event) => setServiceOwner(charge.service_identifier, event.target.value),
      },
    });
    select.append(node("option", { value: "", text: "Choose owner" }));
    participants.forEach((participant) => {
      const option = node("option", {
        value: participant.id,
        text: participant.name || participant.id,
      });
      option.selected = participant.id === selectedOwner;
      select.append(option);
    });
    return select;
  }

  function setServiceOwner(service, owner) {
    if (!state.household) {
      return;
    }
    if (owner) {
      state.household.service_owners[service] = owner;
    } else {
      delete state.household.service_owners[service];
    }
    document.querySelectorAll(".owner-select").forEach((select) => {
      if (select.dataset.serviceKey === service) {
        select.value = owner;
      }
    });
    markHouseholdDirty();
    updateOwnershipState();
  }

  function unresolvedOwnership() {
    const run = state.activeRun;
    if (!run || !run.bill) {
      return [];
    }
    const participantIds = new Set((state.household ? state.household.participants : []).map((participant) => participant.id));
    const unresolved = [];
    run.bill.charges.forEach((charge) => {
      if (charge.scope !== "line") {
        return;
      }
      if (!charge.service_identifier) {
        unresolved.push(`${charge.charge_id} has no service identifier`);
        return;
      }
      const owner = state.household && state.household.service_owners
        ? state.household.service_owners[charge.service_identifier]
        : null;
      if (!owner || !participantIds.has(owner)) {
        unresolved.push(`${charge.service_identifier} needs an owner`);
      }
    });
    return Array.from(new Set(unresolved));
  }

  function updateOwnershipState() {
    const unresolved = unresolvedOwnership();
    const hasParticipants = Boolean(state.household && state.household.participants.length);
    const canPreviewStatus = Boolean(state.activeRun && ["blocked", "needs_review", "ready"].includes(state.activeRun.status));
    if (!canPreviewStatus) {
      setText(refs["ownership-state"], "This run is immutable because its destination outcome has already been recorded.");
      refs["build-preview"].disabled = true;
    } else if (!unresolved.length && hasParticipants) {
      setText(refs["ownership-state"], "Every line has an owner. Preview will allocate shared charges by the saved weights.");
      refs["build-preview"].disabled = false;
    } else {
      setText(refs["ownership-state"], unresolved.length
        ? `${unresolved.length} ownership assignment${unresolved.length === 1 ? "" : "s"} remain.`
        : "Add at least one household participant before previewing.");
      refs["build-preview"].disabled = true;
    }
  }

  function openJsonEditor() {
    if (!state.activeRun || !state.activeRun.bill) {
      return;
    }
    refs["json-editor"].open = true;
    if (!state.jsonEditorDirty) {
      refs["bill-json"].value = JSON.stringify(state.activeRun.bill, null, 2);
    }
    refs["bill-json"].focus();
  }

  async function saveBillCorrection() {
    if (!state.activeRun || !state.activeRun.bill) {
      return;
    }
    let bill;
    try {
      bill = JSON.parse(refs["bill-json"].value);
    } catch (error) {
      refs["json-editor-state"].className = "muted difference-fail";
      setText(refs["json-editor-state"], `Invalid JSON: ${error.message}`);
      return;
    }
    busy(refs["save-bill-json"], true, "Validating…");
    refs["json-editor-state"].className = "muted";
    setText(refs["json-editor-state"], "Running strict schema and deterministic checks…");
    try {
      const payload = await api(`/api/runs/${encodeURIComponent(state.activeRun.id)}/bill`, {
        method: "PUT",
        body: bill,
      });
      state.activeRun = payload.run;
      state.jsonEditorDirty = false;
      refs["json-editor-state"].className = "muted difference-pass";
      setText(refs["json-editor-state"], "Correction saved. Any earlier preview was invalidated.");
      renderActiveRun();
      await refreshRecords();
      toast("Normalized bill corrected and reconciled again.");
    } catch (error) {
      refs["json-editor-state"].className = "muted difference-fail";
      setText(refs["json-editor-state"], error.message);
    } finally {
      busy(refs["save-bill-json"], false);
    }
  }

  async function buildPreview() {
    if (!state.activeRun || !state.activeRun.bill) {
      return;
    }
    if (unresolvedOwnership().length) {
      toast("Assign every line-scoped service before previewing.", "error");
      return;
    }
    busy(refs["build-preview"], true, "Building exact preview…");
    try {
      const household = await saveHousehold({ quiet: true });
      const payload = await api(`/api/runs/${encodeURIComponent(state.activeRun.id)}/preview`, {
        method: "POST",
        body: household,
      });
      state.activeRun = payload.run;
      renderActiveRun();
      await refreshRecords();
      await verifyAudit();
      scrollToSection("preview-section");
      toast(state.activeRun.status === "ready"
        ? "Deterministic preview is ready for your review."
        : "Preview built with blockers; nothing can be posted.",
      state.activeRun.status === "ready" ? "success" : "error");
    } catch (error) {
      showGlobal(error.message, "error");
    } finally {
      busy(refs["build-preview"], false);
    }
  }

  function renderPreview() {
    const run = state.activeRun;
    if (!run || !run.preview) {
      refs["preview-empty"].hidden = false;
      refs["preview-content"].hidden = true;
      updateWorkflow();
      return;
    }
    const preview = run.preview;
    const isLocalSummary = preview.destination === "local_summary";
    refs["preview-empty"].hidden = true;
    refs["preview-content"].hidden = false;
    setText(refs["preview-description"], preview.description || "Splitwise expense");
    setText(refs["preview-cost"], formatMoney(preview.cost, preview.currency_code));
    setText(refs["preview-digest"], run.preview_digest || "—");
    refs["preview-digest"].title = run.preview_digest || "";
    const proofLabel = document.querySelector(".expense-proof .micro-label");
    if (proofLabel) setText(proofLabel, isLocalSummary ? "WhatsApp / local summary" : "Splitwise dry run");
    const metadata = [
      ["Destination", preview.destination || "splitwise"],
      ["Expense date", formatDate(preview.date)],
      ["Group", preview.group_id === null || preview.group_id === undefined ? "Not set" : String(preview.group_id)],
    ];
    replace(refs["preview-meta"], metadata.map(([label, value]) => node("div", {}, [
      node("dt", { text: label }),
      node("dd", { text: value }),
    ])));
    const shares = Array.isArray(preview.shares) ? preview.shares : [];
    replace(refs["preview-shares"], shares.map((share) => node("div", { className: "share-row" }, [
      node("div", {}, [
        node("strong", { text: share.participant_name || share.participant_id }),
        node("small", {
          text: share.paid_share && Number(share.paid_share) !== 0
            ? `Paid ${formatMoney(share.paid_share, preview.currency_code)} · Splitwise ${share.splitwise_user_id || "missing"}`
            : `Splitwise ${share.splitwise_user_id || "missing"}`,
        }),
      ]),
      node("code", { text: formatMoney(share.owed_share, preview.currency_code) }),
    ])));
    const blockers = Array.from(new Set([
      ...(Array.isArray(preview.blockers) ? preview.blockers : []),
      ...(run.gate && Array.isArray(run.gate.destination_blockers) ? run.gate.destination_blockers : []),
    ]));
    refs["preview-blockers"].hidden = blockers.length === 0;
    if (blockers.length) {
      const list = node("ul");
      blockers.forEach((blocker) => list.append(node("li", { text: blocker })));
      replace(refs["preview-blockers"], [
        node("strong", { text: "Destination blockers" }),
        list,
      ]);
    } else {
      replace(refs["preview-blockers"], []);
    }
    const canPost = !isLocalSummary && run.status === "ready" && blockers.length === 0;
    refs["open-confirmation"].disabled = !canPost;
    setText(refs["open-confirmation"], isLocalSummary ? "Splitwise not connected" : "Review & confirm post");
    if (run.status === "ready") {
      setText(refs["approval-title"], isLocalSummary ? "Local result ready to share" : (canPost ? "Ready for your decision" : "Preview is blocked"));
      setText(refs["approval-copy"], isLocalSummary
        ? "Copy the WhatsApp summary above. Nothing is sent anywhere."
        : (canPost ? "The preview is still local and has not been sent to Splitwise." : "Resolve every blocker and rebuild this preview."));
    } else if (["posted", "posted_unverified"].includes(run.status)) {
      setText(refs["approval-title"], "This preview was already posted");
      setText(refs["approval-copy"], "Use the verified app-created record below if a rollback is needed.");
    } else if (run.status === "ambiguous") {
      setText(refs["approval-title"], "Do not retry this post");
      setText(refs["approval-copy"], "Check Splitwise manually using the correlation ID before taking any action.");
    } else if (run.status === "rollback_ambiguous") {
      setText(refs["approval-title"], "Do not retry this rollback");
      setText(refs["approval-copy"], "Check whether the recorded Splitwise expense still exists; the deletion result is unknown.");
    } else if (run.status === "rolled_back") {
      setText(refs["approval-title"], "Rollback recorded");
      setText(refs["approval-copy"], "The app-created Splitwise expense was deleted; the local audit record remains.");
    } else {
      setText(refs["approval-title"], "External posting unavailable");
      setText(refs["approval-copy"], statusLabel(run.status));
    }
    renderPostingOutcome();
    updateWorkflow();
  }

  function renderPostingOutcome() {
    const posting = state.activeRun ? state.activeRun.posting : null;
    refs["posting-outcome"].hidden = !posting;
    replace(refs["posting-outcome"], []);
    if (!posting) {
      refs["posting-outcome"].className = "posting-outcome";
      return;
    }
    let title = "Destination record";
    let copy = "The destination adapter returned a result.";
    let tone = "";
    if (posting.status === "posted") {
      title = "Verified at Splitwise";
      copy = "The created expense was read back and matched the exact preview total, currency, participants, and correlation reference.";
    } else if (posting.status === "posted_unverified") {
      title = "Created, but verification is incomplete";
      copy = "Do not post again. Inspect the expense in Splitwise before deciding whether to keep or roll it back.";
      tone = " is-warn";
    } else if (posting.status === "ambiguous") {
      title = "Ambiguous destination outcome — do not retry";
      copy = "WaySplit cannot prove whether Splitwise created the expense. Search Splitwise using the correlation ID and resolve it manually.";
      tone = " is-warn";
    } else if (posting.status === "rollback_ambiguous") {
      title = "Ambiguous rollback outcome — do not retry";
      copy = "WaySplit cannot prove whether Splitwise deleted the expense. Inspect the exact recorded expense ID before manual resolution.";
      tone = " is-warn";
    } else if (posting.status === "failed") {
      title = "Posting was not verified";
      copy = "Read the destination message before creating a new one-time confirmation. The preview remains unchanged.";
      tone = " is-danger";
    } else if (posting.status === "rolled_back") {
      title = "Rollback verified";
      copy = "The app-created Splitwise expense was deleted. Its tamper-evident local audit record remains.";
    } else if (posting.status === "submitting") {
      title = "Submission in progress";
      copy = "Wait for a verified result. Retrying now could create a duplicate expense.";
      tone = " is-warn";
    } else if (posting.status === "rollback_submitting") {
      title = "Rollback in progress";
      copy = "Wait for a verified result. Retrying a deletion with an unknown outcome is blocked.";
      tone = " is-warn";
    }
    refs["posting-outcome"].className = `posting-outcome${tone}`;
    const content = [
      node("h3", { text: title }),
      node("p", { text: copy }),
      node("div", { className: "posting-facts" }, [
        node("span", { text: `Correlation ${posting.correlation_id || "—"}` }),
        node("span", { text: `Expense ${posting.external_id || "not confirmed"}` }),
        node("span", { text: `Updated ${formatDate(posting.updated_at, true)}` }),
      ]),
    ];
    const summary = posting.response_summary || {};
    const issues = Array.isArray(summary.verification_issues) ? summary.verification_issues : [];
    if (summary.message) {
      content.push(node("p", { text: summary.message }));
    }
    if (issues.length) {
      const list = node("ul");
      issues.forEach((issue) => list.append(node("li", { text: issue })));
      content.push(list);
    }
    if (["posted", "posted_unverified"].includes(posting.status)) {
      content.push(node("div", { className: "posting-actions" }, [
        node("p", { text: "Rollback is limited to the expense created by this run and requires typing DELETE." }),
        node("button", {
          className: "danger-button",
          type: "button",
          text: "Rollback app-created expense",
          on: { click: openRollback },
        }),
      ]));
    }
    replace(refs["posting-outcome"], content);
  }

  async function openConfirmation() {
    const run = state.activeRun;
    if (!run || run.status !== "ready" || !run.preview) {
      toast("Build a passing preview before confirming.", "error");
      return;
    }
    state.confirmationToken = null;
    refs["ack-preview"].checked = false;
    refs["accept-terms"].checked = false;
    refs["splitwise-token"].value = "";
    refs["splitwise-token"].placeholder = state.splitwiseToken
      ? "Connected token will be used"
      : "Paste a Splitwise token";
    refs["confirmation-state"].className = "dialog-state";
    setText(refs["confirmation-state"], "Issuing a one-time approval for this preview…");
    refs["confirm-post"].disabled = true;
    replace(refs["confirmation-summary"], [
      node("strong", { text: run.preview.description || "Splitwise expense" }),
      node("strong", { text: formatMoney(run.preview.cost, run.preview.currency_code) }),
      node("span", { text: "Loading the immutable destination payload…" }),
    ]);
    refs["confirmation-dialog"].showModal();
    try {
      const payload = await api(`/api/runs/${encodeURIComponent(run.id)}/confirmation`, { method: "POST" });
      state.confirmationToken = payload.confirmation_token;
      const target = payload.target || {};
      replace(refs["confirmation-summary"], [
        node("strong", { text: run.preview.description || "Splitwise expense" }),
        node("strong", { text: formatMoney(run.preview.cost, run.preview.currency_code) }),
        node("span", { text: `${(run.preview.shares || []).length} participant shares · destination ${target.destination || "splitwise"}` }),
        node("span", { text: `Bill fingerprint ${target.bill_fingerprint || "unavailable"}` }),
        node("code", { text: target.preview_digest || run.preview_digest || "Digest unavailable", title: target.preview_digest || run.preview_digest || "" }),
        node("pre", {
          className: "exact-payload",
          text: JSON.stringify(target.payload || {}, null, 2),
          ariaLabel: "Exact Splitwise payload",
        }),
      ]);
      setText(refs["confirmation-state"], "One-time approval ready. It expires in 15 minutes and is bound to this digest.");
      refs["confirm-post"].disabled = false;
    } catch (error) {
      refs["confirmation-state"].className = "dialog-state is-error";
      setText(refs["confirmation-state"], error.message);
    }
  }

  async function postConfirmedPreview(event) {
    event.preventDefault();
    if (!state.activeRun || !state.confirmationToken) {
      return;
    }
    if (!refs["ack-preview"].checked || !refs["accept-terms"].checked) {
      refs["confirmation-state"].className = "dialog-state is-error";
      setText(refs["confirmation-state"], "Both explicit acknowledgements are required.");
      return;
    }
    const runId = state.activeRun.id;
    const accessToken = refs["splitwise-token"].value || state.splitwiseToken;
    const request = {
      confirmation_token: state.confirmationToken,
      access_token: accessToken || null,
      acknowledged_preview: true,
      accepted_destination_terms: true,
    };
    refs["splitwise-token"].value = "";
    busy(refs["confirm-post"], true, "Posting once…");
    refs["confirmation-state"].className = "dialog-state";
    setText(refs["confirmation-state"], "Waiting for Splitwise and verifying the returned expense…");
    try {
      await api(`/api/runs/${encodeURIComponent(runId)}/post`, { method: "POST", body: request });
      state.confirmationToken = null;
      closeDialog(refs["confirmation-dialog"]);
      await loadRun(runId);
      await refreshRecords();
      await verifyAudit();
      scrollToSection("preview-section");
      toast(state.activeRun.status === "posted"
        ? "Splitwise expense posted and verified."
        : "Expense posted; destination verification needs attention.",
      state.activeRun.status === "posted" ? "success" : "error");
    } catch (error) {
      state.confirmationToken = null;
      if (error.payload && error.payload.error === "destination_ambiguous") {
        closeDialog(refs["confirmation-dialog"]);
        await loadRun(runId);
        await refreshRecords();
        scrollToSection("preview-section");
        showGlobal("Destination outcome is ambiguous. Do not retry; inspect Splitwise using the recorded correlation ID.", "error");
      } else {
        refs["confirmation-state"].className = "dialog-state is-error";
        setText(refs["confirmation-state"], `${error.message} Close and reopen this dialog for a new one-time approval.`);
      }
    } finally {
      refs["splitwise-token"].value = "";
      busy(refs["confirm-post"], false);
      if (!state.confirmationToken) {
        refs["confirm-post"].disabled = true;
      }
    }
  }

  function updateRollbackButton() {
    refs["confirm-rollback"].disabled = !state.rollbackConfirmationToken ||
      refs["rollback-phrase"].value !== "DELETE" ||
      !refs["ack-rollback-target"].checked;
  }

  async function openRollback() {
    if (!state.activeRun || !state.activeRun.posting ||
        !["posted", "posted_unverified"].includes(state.activeRun.posting.status)) {
      toast("No completed app-created expense is available to roll back.", "error");
      return;
    }
    state.rollbackConfirmationToken = null;
    refs["rollback-phrase"].value = "";
    refs["ack-rollback-target"].checked = false;
    refs["rollback-token"].value = "";
    refs["rollback-token"].placeholder = state.splitwiseToken
      ? "Connected token will be used"
      : "Paste a Splitwise token";
    refs["confirm-rollback"].disabled = true;
    refs["rollback-state"].className = "dialog-state";
    replace(refs["rollback-summary"], [
      node("span", { text: "Loading the immutable rollback target…" }),
    ]);
    setText(refs["rollback-state"], "Issuing a one-time rollback approval…");
    refs["rollback-dialog"].showModal();
    try {
      const payload = await api(
        `/api/runs/${encodeURIComponent(state.activeRun.id)}/rollback-confirmation`,
        { method: "POST" },
      );
      state.rollbackConfirmationToken = payload.confirmation_token;
      const target = payload.target || {};
      replace(refs["rollback-summary"], [
        node("strong", { text: `Expense ${target.external_id || "unavailable"}` }),
        node("strong", { text: target.posting_status || "posted" }),
        node("span", { text: `Destination ${target.destination || "splitwise"} · reference ${target.correlation_id || "unavailable"}` }),
        node("code", { text: target.preview_digest || "Digest unavailable", title: target.preview_digest || "" }),
        node("pre", {
          className: "exact-payload",
          text: JSON.stringify(target.payload || {}, null, 2),
          ariaLabel: "Exact Splitwise expense targeted for deletion",
        }),
      ]);
      setText(refs["rollback-state"], "One-time rollback approval ready. The target will be verified again before deletion.");
      updateRollbackButton();
    } catch (error) {
      refs["rollback-state"].className = "dialog-state is-error";
      setText(refs["rollback-state"], error.message);
    }
  }

  async function rollbackPosting(event) {
    event.preventDefault();
    if (!state.activeRun || !state.rollbackConfirmationToken ||
        refs["rollback-phrase"].value !== "DELETE" ||
        !refs["ack-rollback-target"].checked) {
      return;
    }
    const runId = state.activeRun.id;
    const accessToken = refs["rollback-token"].value || state.splitwiseToken;
    const request = {
      confirmation_token: state.rollbackConfirmationToken,
      access_token: accessToken || null,
      confirmation_phrase: "DELETE",
      acknowledged_target: true,
    };
    refs["rollback-token"].value = "";
    busy(refs["confirm-rollback"], true, "Deleting once…");
    setText(refs["rollback-state"], "Waiting for Splitwise to confirm deletion…");
    try {
      await api(`/api/runs/${encodeURIComponent(runId)}/rollback`, { method: "POST", body: request });
      state.rollbackConfirmationToken = null;
      closeDialog(refs["rollback-dialog"]);
      await loadRun(runId);
      await refreshRecords();
      await verifyAudit();
      toast("App-created Splitwise expense deleted and rollback audited.");
    } catch (error) {
      state.rollbackConfirmationToken = null;
      if (error.payload && error.payload.error === "destination_ambiguous") {
        closeDialog(refs["rollback-dialog"]);
        await loadRun(runId);
        await refreshRecords();
        showGlobal("Rollback outcome is ambiguous. Do not retry; inspect the recorded Splitwise expense ID and resolve it manually.", "error");
      } else {
        refs["rollback-state"].className = "dialog-state is-error";
        setText(refs["rollback-state"], `${error.message} Close and reopen this dialog for a new one-time approval.`);
      }
    } finally {
      refs["rollback-token"].value = "";
      busy(refs["confirm-rollback"], false);
      updateRollbackButton();
    }
  }

  function closeDialog(dialog) {
    if (dialog && dialog.open) {
      dialog.close();
    }
  }

  function clearDialogSecrets(dialog) {
    if (dialog === refs["confirmation-dialog"]) {
      refs["splitwise-token"].value = "";
      refs["ack-preview"].checked = false;
      refs["accept-terms"].checked = false;
      state.confirmationToken = null;
    } else if (dialog === refs["rollback-dialog"]) {
      state.rollbackConfirmationToken = null;
      refs["rollback-token"].value = "";
      refs["rollback-phrase"].value = "";
      refs["ack-rollback-target"].checked = false;
      refs["confirm-rollback"].disabled = true;
    }
  }

  async function loadRuns() {
    try {
      const payload = await api("/api/runs");
      state.runs = Array.isArray(payload.runs) ? payload.runs : [];
      renderHistory();
    } catch (error) {
      replace(refs["run-history"], [node("div", { className: "notice is-error", text: error.message })]);
    }
  }

  function historyTone(status) {
    if (["posted", "ready", "rolled_back"].includes(status)) {
      return "is-success";
    }
    if (["blocked", "failed"].includes(status)) {
      return "is-danger";
    }
    if (["ambiguous", "rollback_ambiguous", "posted_unverified", "needs_review"].includes(status)) {
      return "is-warn";
    }
    return "";
  }

  function renderHistory() {
    setText(refs["history-count"], `${state.runs.length} recent run${state.runs.length === 1 ? "" : "s"}`);
    refs["history-empty"].hidden = state.runs.length > 0;
    const items = state.runs.map((run) => {
      const title = run.bill
        ? `${run.bill.issuer.name} · ${formatMoney(run.bill.totals.current_charges, run.bill.account.currency)}`
        : run.source_name || "Local statement";
      const current = state.activeRun && state.activeRun.id === run.id;
      return node("button", {
        className: `run-history-item${current ? " is-current" : ""}`,
        type: "button",
        attrs: { "aria-label": `Open ${title}, ${statusLabel(run.status)}` },
        on: {
          click: async () => {
            await loadRun(run.id);
            scrollToSection(run.preview ? "preview-section" : "review-section");
          },
        },
      }, [
        node("span", {}, [
          node("strong", { text: title }),
          node("small", { text: run.source_name || shortHash(run.source_sha256, 12) }),
        ]),
        node("time", { text: formatDate(run.created_at), attrs: { datetime: run.created_at || "" } }),
        node("code", { text: run.model ? run.model.name : "—" }),
        node("span", { className: `history-status ${historyTone(run.status)}`, text: statusLabel(run.status) }),
      ]);
    });
    replace(refs["run-history"], items);
  }

  async function verifyAudit() {
    busy(refs["verify-audit"], true, "Verifying…");
    try {
      const audit = await api("/api/audit/verify");
      renderAudit(audit);
    } catch (error) {
      renderAudit({ valid: false, entries_checked: 0, head_hash: "—", reason: error.message });
    } finally {
      busy(refs["verify-audit"], false);
    }
  }

  function renderAudit(audit) {
    const valid = Boolean(audit.valid);
    refs["audit-chip"].className = `status-chip is-${valid ? "good" : "bad"}`;
    replace(refs["audit-chip"], [
      node("span", { className: "status-dot", attrs: { "aria-hidden": "true" } }),
      node("span", { text: valid ? "Audit chain intact" : "Audit chain warning" }),
    ]);
    refs["audit-card"].className = `audit-card ${valid ? "is-valid" : "is-invalid"}`;
    setText(refs["audit-title"], valid ? "Chain verified" : "Verification failed");
    setText(refs["audit-copy"], valid
      ? "Every stored event hash links to the event before it."
      : audit.reason || "A local audit link did not verify.");
    const facts = [
      ["Entries checked", String(audit.entries_checked || 0)],
      ["Head hash", shortHash(audit.head_hash, 22)],
    ];
    if (audit.failure_sequence) {
      facts.push(["Failure sequence", String(audit.failure_sequence)]);
    }
    replace(refs["audit-details"], facts.flatMap(([label, value]) => [
      node("dt", { text: label }),
      node("dd", { text: value, title: label === "Head hash" ? audit.head_hash : value }),
    ]));
  }

  async function refreshRecords() {
    await Promise.all([loadRuns(), verifyAudit()]);
  }

  function setStep(key, label, mode) {
    const copy = document.querySelector(`[data-step-state="${key}"]`);
    const button = copy ? copy.closest(".rail-step") : null;
    if (copy) {
      setText(copy, label);
    }
    if (button) {
      button.classList.toggle("is-complete", mode === "complete");
      button.classList.toggle("is-blocked", mode === "blocked");
    }
  }

  function updateWorkflow() {
    if (hasReadyAttestation()) {
      setStep("model", "Ready", "complete");
    } else if (state.readiness) {
      setStep("model", "Probe failed", "blocked");
    } else if (state.endpoints.length) {
      setStep("model", state.selectedModel ? "Probe required" : "Choose model", "pending");
    } else {
      setStep("model", "Discovering", "pending");
    }

    if (state.householdSaved) {
      setStep("household", `${state.household.participants.length} saved`, "complete");
    } else {
      setStep("household", "Not saved", "pending");
    }

    const run = state.activeRun;
    if (!run) {
      setStep("statement", "Waiting", "pending");
      setStep("review", "No extraction", "pending");
      setStep("preview", "No preview", "pending");
      return;
    }
    if (run.status === "failed" && !run.bill) {
      setStep("statement", "Failed safely", "blocked");
    } else if (isPollingStatus(run.status)) {
      setStep("statement", statusLabel(run.status), "pending");
    } else {
      setStep("statement", "Extracted", "complete");
    }

    if (!run.bill) {
      setStep("review", "No extraction", "pending");
    } else if (run.status === "blocked" && !run.preview) {
      setStep("review", "Gate blocked", "blocked");
    } else if (run.reconciliation && run.reconciliation.reconciled) {
      setStep("review", "Reconciled", "complete");
    } else {
      setStep("review", "Needs correction", "blocked");
    }

    if (!run.preview) {
      setStep("preview", "No preview", "pending");
    } else if (run.status === "posted") {
      setStep("preview", "Posted & verified", "complete");
    } else if (run.status === "posted_unverified") {
      setStep("preview", "Verify manually", "blocked");
    } else if (run.status === "ambiguous") {
      setStep("preview", "Do not retry", "blocked");
    } else if (run.status === "rollback_ambiguous") {
      setStep("preview", "Rollback unknown", "blocked");
    } else if (run.status === "rolled_back") {
      setStep("preview", "Rolled back", "complete");
    } else if (run.status === "ready") {
      setStep("preview", "Awaiting approval", "complete");
    } else if (run.status === "blocked") {
      setStep("preview", "Blocked", "blocked");
    } else {
      setStep("preview", statusLabel(run.status), "pending");
    }
  }

  async function init() {
    cacheReferences();
    bindEvents();
    renderSelectedFile();
    await Promise.allSettled([
      loadHealth(),
      verifyAudit(),
      loadHousehold(),
      loadRuns(),
      discoverModels(),
    ]);
    updateWorkflow();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
}());
