/* --- Session context (opened from /sessions/<id>) --- */

const q = new URLSearchParams(location.search);
const SESSION_ID = q.get("session");

renderRegionControls(document.getElementById("region-controls"), {
  name: true,
  nameValue: q.get("name"),
});

const envA = document.getElementById("env-a");
const envB = document.getElementById("env-b");
const serviceSel = document.getElementById("service");
const secretWrap = document.getElementById("secret-wrap");
const compareBtn = document.getElementById("compare-btn");
const result = document.getElementById("result");

function sessionUpdate(fields) {
  if (!SESSION_ID || !data || !data.name) return Promise.resolve();
  return fetch(
    "/api/sessions/" + encodeURIComponent(SESSION_ID) + "/items",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: data.name, ...fields }),
    },
  ).catch(() => null);
}

function syncSecretOption() {
  secretWrap.hidden = serviceSel.value !== "ssm";
}
serviceSel.addEventListener("change", syncSecretOption);
syncSecretOption();

loadEnvs(envB, envA)
  .then(() => applyQueryParams())
  .catch((e) => {
    result.innerHTML = renderError("No se pudieron cargar los ambientes: " + e.message);
  });

function applyQueryParams() {
  const name = q.get("name");
  const envAq = q.get("env_a");
  const envBq = q.get("env_b");
  const service = q.get("service");
  const withSecret = q.get("with_secret");

  const sessionBack = document.getElementById("session-back");
  if (SESSION_ID && sessionBack) {
    sessionBack.style.display = "inline-block";
    sessionBack.href = "/sessions/" + encodeURIComponent(SESSION_ID);
  }

  if (name != null) document.getElementById("name").value = name;
  if (envAq) envA.value = envAq;
  if (envBq) envB.value = envBq;
  if (service === "ssm" || service === "secretsmanager") serviceSel.value = service;
  if (withSecret && withSecret !== "0" && serviceSel.value === "ssm") {
    document.getElementById("with-secret").checked = true;
  }
  syncSecretOption();

  if (name != null && name.trim()) {
    compareBtn.click();
  }
}

/* --- Interactive change selection state --- */

let data = null;
let rows = [];
let isJSON = false;
let pairState = null; // { param: {text, include}, secret: {text, include} }
let scriptText = "";
let applyTimer = null;
let applySeq = 0;

function parseValue(text) {
  const t = String(text).trim();
  if (t === "") return "";
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

function getParts(path) {
  return path.replace(/^\$/, "").split(".").filter((s) => s !== "");
}

function clone(v) {
  return JSON.parse(JSON.stringify(v));
}

function setAt(obj, parts, value) {
  if (!parts.length) return value;
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const seg = parts[i];
    const nextIsIndex = /^\d+$/.test(parts[i + 1]);
    if (cur[seg] == null || typeof cur[seg] !== "object") {
      cur[seg] = nextIsIndex ? [] : {};
    }
    cur = cur[seg];
  }
  cur[parts[parts.length - 1]] = value;
  return obj;
}

function deleteAt(obj, parts) {
  if (!parts.length) return {};
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const seg = parts[i];
    if (cur == null || cur[seg] == null) return obj;
    cur = cur[seg];
  }
  const last = parts[parts.length - 1];
  if (Array.isArray(cur) && /^\d+$/.test(last)) {
    cur.splice(Number(last), 1);
  } else {
    delete cur[last];
  }
  return obj;
}

function mergedValue() {
  const selected = rows.filter((r) => r.include);
  if (!isJSON) {
    const whole = selected.find((r) => r.path === "$");
    return whole ? parseValue(whole.text) : "";
  }
  const base = clone(parseValue(data.value_a ?? ""));
  const dels = selected
    .filter((r) => r.op === "del")
    .map((r) => getParts(r.path))
    .sort((a, b) => {
      const la = a[a.length - 1] || "";
      const lb = b[b.length - 1] || "";
      const na = /^\d+$/.test(la);
      const nb = /^\d+$/.test(lb);
      if (na && nb) return Number(lb) - Number(la);
      return 0;
    });
  for (const parts of dels) deleteAt(base, parts);
  for (const r of selected) {
    if (r.op !== "del") setAt(base, getParts(r.path), parseValue(r.text));
  }
  return base;
}

function serializeMerged() {
  const v = mergedValue();
  return isJSON ? JSON.stringify(v, null, 2) : String(v);
}

function state() {
  return {
    key: data.source_marker || "",
    _i: Math.random(),
  };
}

function refreshScript() {
  const seq = ++applySeq;
  const newValue = serializeMerged();
  const pre = document.getElementById("script-pre");
  fetch("/api/params/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      env_a: data.env_a,
      env_b: data.env_b,
      service: data.service,
      name: data.name,
      new_value: newValue,
      value_type: data.value_type_b || "String",
    }),
  })
    .then(async (res) => {
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      return body;
    })
    .then((body) => {
      if (seq !== applySeq) return;
      scriptText = body.script;
      if (pre) pre.textContent = scriptText;
    })
    .catch((e) => {
      if (seq !== applySeq) return;
      scriptText = "";
      if (pre) pre.textContent = "No se pudo generar el comando: " + e.message;
    });
}

function onEdit() {
  const preview = document.getElementById("preview-pre");
  if (preview) preview.textContent = serializeMerged();
  clearTimeout(applyTimer);
  applyTimer = setTimeout(refreshScript, 350);
}

function makeCopyBtn() {
  const btn = document.createElement("button");
  btn.className = "secondary";
  btn.textContent = "Copiar comando";
  btn.addEventListener("click", async () => {
    const ok = await copyText(scriptText);
    btn.textContent = ok ? "¡Copiado!" : "Error al copiar";
    setTimeout(() => (btn.textContent = "Copiar comando"), 1500);
  });
  return btn;
}

function makePreviewCopyBtn() {
  const btn = document.createElement("button");
  btn.className = "secondary";
  btn.textContent = "Copiar valor";
  btn.addEventListener("click", async () => {
    const pre = document.getElementById("preview-pre");
    const ok = await copyText(pre ? pre.textContent : "");
    btn.textContent = ok ? "¡Copiado!" : "Error al copiar";
    setTimeout(() => (btn.textContent = "Copiar valor"), 1500);
  });
  return btn;
}

function renderExecResult(message, isError) {
  const exec = document.getElementById("exec-result");
  if (exec) {
    exec.innerHTML = `<div class="note ${isError ? "err" : "ok"}">${isError ? "✗" : "✓"} ${escapeHtml(message)}</div>`;
  }
}

function makeExecuteBtn(op, getValue, target = "a", label = null) {
  const btn = document.createElement("button");
  btn.className = "primary";
  const verb = op === "delete" ? "Eliminar" : "Ejecutar";
  const dest = target === "b" ? data.env_b : data.env_a;
  btn.textContent = label || `${verb} en ${dest}`;
  btn.addEventListener("click", async () => {
    const msg =
      op === "delete"
        ? `¿Eliminar DEFINITIVAMENTE ${data.name} en ${dest}?\nNo se puede deshacer.`
        : `¿Ejecutar la actualización de ${data.name} en ${dest}?`;
    if (!confirm(msg)) return;
    renderExecResult("Ejecutando…");
    btn.disabled = true;
    try {
      const res = await fetch("/api/params/apply-execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          env_a: data.env_a,
          env_b: data.env_b,
          service: data.service,
          op,
          target,
          name: data.name,
          new_value: op === "update" ? getValue() : "",
          value_type: data.value_type_b || "String",
          confirm: true,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      renderExecResult(body.message);
      if (SESSION_ID) {
        sessionUpdate({
          status: "aplicado",
          service: data.service,
          is_secret: !!data.pair,
          script: scriptText,
          notes: body.message,
        });
      }
      setTimeout(() => compareBtn.click(), 700);
    } catch (e) {
      renderExecResult(e.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
}

/* --- Create-from-scratch (parámetro que no existe en origen y/o destino) --- */

let createTarget = "b";

function refreshCreateScript() {
  const seq = ++applySeq;
  const pre = document.getElementById("script-pre");
  const ta = document.getElementById("create-value");
  const value = ta ? ta.value : "";
  fetch("/api/params/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      env_a: data.env_a,
      env_b: data.env_b,
      service: data.service,
      name: data.name,
      new_value: value,
      value_type: data.value_type_b || "String",
      target: createTarget,
    }),
  })
    .then(async (res) => {
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      return body;
    })
    .then((body) => {
      if (seq !== applySeq) return;
      scriptText = body.script || "";
      if (pre)
        pre.textContent = scriptText || "Escribí un valor para generar el comando.";
    })
    .catch((e) => {
      if (seq !== applySeq) return;
      scriptText = "";
      if (pre) pre.textContent = "No se pudo generar el comando: " + e.message;
    });
}

function createOnEdit() {
  clearTimeout(applyTimer);
  applyTimer = setTimeout(refreshCreateScript, 350);
}

function makeCreateExecuteBtn() {
  const btn = document.createElement("button");
  btn.className = "primary";
  const region = createTarget === "a" ? data.env_a : data.env_b;
  btn.textContent = `Crear en ${region}`;
  btn.addEventListener("click", async () => {
    const value = document.getElementById("create-value").value;
    if (value.trim() === "") {
      renderExecResult("Escribí un valor para crear el parámetro.", true);
      return;
    }
    const verb = data.status === "missing_in_a" ? "actualizar/crear" : "crear";
    if (!confirm(`¿${verb} ${data.name} en ${region} con el valor ingresado?`))
      return;
    renderExecResult("Creando…");
    btn.disabled = true;
    try {
      const res = await fetch("/api/params/apply-execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          env_a: data.env_a,
          env_b: data.env_b,
          service: data.service,
          op: "update",
          target: createTarget,
          name: data.name,
          new_value: value,
          value_type: data.value_type_b || "String",
          confirm: true,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      renderExecResult(body.message);
      if (SESSION_ID) {
        sessionUpdate({
          status: "aplicado",
          service: data.service,
          is_secret: false,
          script: scriptText,
          notes: body.message,
        });
      }
      setTimeout(() => compareBtn.click(), 700);
    } catch (e) {
      renderExecResult(e.message, true);
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
}

/* --- "Es un secreto" pair mode (SSM parameter + paired secret) --- */

function buildPairPreview() {
  const parts = [];
  if (pairState.secret.include)
    parts.push(`Secreto resultante (Secrets Manager):\n${pairState.secret.text}`);
  if (pairState.param.include)
    parts.push(`Parámetro resultante (SSM):\n${pairState.param.text}`);
  return parts.join("\n\n");
}

function refreshPairScript() {
  const seq = ++applySeq;
  const pre = document.getElementById("script-pre");
  fetch("/api/params/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      env_a: data.env_a,
      env_b: data.env_b,
      service: data.service,
      name: data.name,
      with_secret: true,
      write_secret: pairState.secret.include,
      write_param: pairState.param.include,
      new_value: pairState.param.include ? pairState.param.text : (data.value_a ?? ""),
      value_type: data.value_type_b || "String",
      new_secret_value: pairState.secret.include ? pairState.secret.text : (data.secret_value_a ?? ""),
    }),
  })
    .then(async (res) => {
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      return body;
    })
    .then((body) => {
      if (seq !== applySeq) return;
      scriptText = body.script;
      if (pre) pre.textContent = scriptText;
    })
    .catch((e) => {
      if (seq !== applySeq) return;
      scriptText = "";
      if (pre) pre.textContent = "No se pudo generar el comando: " + e.message;
    });
}

function pairOnEdit() {
  const preview = document.getElementById("preview-pre");
  if (preview) preview.textContent = buildPairPreview();
  clearTimeout(applyTimer);
  applyTimer = setTimeout(refreshPairScript, 350);
}

function buildStepRows() {
  const defs = [
    { key: "secret", label: "Secreto (Secrets Manager)", current: data.secret_value_a ?? "", status: data.secret_status },
    { key: "param", label: "Parámetro (SSM)", current: data.value_a ?? "", status: data.param_status },
  ];
  return defs
    .filter((d) => (d.key === "secret" ? data.secret_needs_write : data.param_needs_write))
    .map((d) => {
      const op = d.status === "missing_in_a" ? "crear" : "actualizar";
      const badge = `<span class="badge ${d.status === "missing_in_a" ? "missing_in_a" : "different"}">${op}</span>`;
      return `<tr>
        <td><input type="checkbox" class="pair-include" data-pair="${d.key}" checked></td>
        <td><code>${escapeHtml(d.label)}</code></td>
        <td>${badge}</td>
        <td>${formatValue(d.current || null)}</td>
        <td><textarea class="change-input pair-input" data-pair="${d.key}" rows="1" spellcheck="false">${escapeHtml(pairState[d.key].text)}</textarea></td>
      </tr>`;
    })
    .join("");
}

function makePairExecuteBtn() {
  const btn = document.createElement("button");
  btn.className = "primary";
  btn.textContent = `Ejecutar en ${data.env_a}`;
  btn.addEventListener("click", async () => {
    const stepsMsg = [
      pairState.secret.include ? "Paso 1 — actualizar el SECRETO en Secrets Manager" : "",
      pairState.param.include ? "Paso 2 — actualizar el PARÁMETRO en SSM" : "",
    ].filter(Boolean).join("\n");
    const msg =
      `¿Sincronizar ${data.name} en ${data.env_a}?\n${stepsMsg}\n\n` +
      "Se ejecutan en ese orden: nunca en una sola operación.";
    if (!confirm(msg)) return;
    const exec = document.getElementById("exec-result");
    if (exec) exec.innerHTML = '<div class="note">Ejecutando…</div>';
    btn.disabled = true;
    try {
      const res = await fetch("/api/params/apply-execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          env_a: data.env_a,
          env_b: data.env_b,
          service: data.service,
          op: "update",
          name: data.name,
          with_secret: true,
          write_secret: pairState.secret.include,
          write_param: pairState.param.include,
          new_value: pairState.param.include ? pairState.param.text : (data.value_a ?? ""),
          value_type: data.value_type_b || "String",
          new_secret_value: pairState.secret.include ? pairState.secret.text : (data.secret_value_a ?? ""),
          confirm: true,
        }),
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      const lines = (body.steps || [])
        .map((s) => `✓ ${escapeHtml(s.message)}`)
        .join("<br>");
      if (exec) exec.innerHTML = `<div class="note ok">${lines}</div>`;
      if (SESSION_ID) {
        sessionUpdate({
          status: "aplicado",
          service: "ssm",
          is_secret: true,
          script: scriptText,
          notes: (body.steps || []).map((s) => s.message).join(" · "),
        });
      }
      setTimeout(() => compareBtn.click(), 700);
    } catch (e) {
      if (exec) exec.innerHTML = `<div class="note err">✗ ${escapeHtml(e.message)}</div>`;
    } finally {
      btn.disabled = false;
    }
  });
  return btn;
}

function renderPair(parts) {
  const showA = data.value_a ?? "";
  const showB = data.value_b ?? "";
  parts.push(
    `<div class="columns">
       <div class="panel">
         <div class="section-title"><strong>Región de Origen — ${escapeHtml(data.env_b)}</strong></div>
         <div class="muted" style="font-size:12px;">Parámetro</div>
         ${preBlock(showB, "No existe")}
         <div class="muted" style="font-size:12px; margin-top:8px;">Secreto</div>
         ${preBlock(data.secret_value_b ?? "", "No existe")}
       </div>
       <div class="panel">
         <div class="section-title"><strong>Región Destino — ${escapeHtml(data.env_a)}</strong></div>
         <div class="muted" style="font-size:12px;">Parámetro</div>
         ${preBlock(showA, "No existe")}
         <div class="muted" style="font-size:12px; margin-top:8px;">Secreto</div>
         ${preBlock(data.secret_value_a ?? "", "No existe")}
       </div>
     </div>`,
  );

  if (!data.param_needs_write && !data.secret_needs_write) return;

  parts.push(
    `<div class="panel">
       <div class="section-title">
         <strong>Cambios</strong>
         <span class="muted">(se actualiza primero el secreto, luego el parámetro — nunca en una sola operación)</span>
       </div>
       <table>
         <thead><tr><th></th><th>Recurso</th><th>Operación</th><th>Valor actual en la región destino</th><th>Valor a aplicar</th></tr></thead>
         <tbody>${buildStepRows()}</tbody>
       </table>
     </div>
     <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; margin-top:14px;">
       <div class="panel">
         <div class="section-title"><strong>Valores resultantes hacia la región destino (${escapeHtml(data.env_a)})</strong></div>
         <pre id="preview-pre" class="script-block"></pre>
         <div class="actions" style="margin-top:10px;"><span id="preview-actions"></span></div>
       </div>
       <div class="panel script-block">
         <div class="section-title"><strong>Comandos de actualización (orden: secreto → parámetro)</strong></div>
         <pre id="script-pre" class="script-block">Regenerando…</pre>
         <div class="actions" style="margin-top:10px;"><span id="script-actions"></span><span id="exec-actions"></span></div>
         <div id="exec-result"></div>
       </div>
     </div>`,
  );
}

function attachPairHandlers() {
  document.querySelectorAll(".pair-include").forEach((cb) =>
    cb.addEventListener("change", () => {
      pairState[cb.dataset.pair].include = cb.checked;
      pairOnEdit();
    }),
  );
  document.querySelectorAll(".pair-input").forEach((ta) =>
    ta.addEventListener("input", () => {
      pairState[ta.dataset.pair].text = ta.value;
      pairOnEdit();
    }),
  );
}

/* --- Rendering --- */

function render(payload) {
  clearTimeout(applyTimer);
  data = payload;
  const parts = [];

  parts.push(
    `<div class="panel" style="margin-top:18px;">
       <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
         <span>${statusBadge(data.status)}</span>
         <span class="muted">${escapeHtml(data.name)}</span>
         <span class="muted">${escapeHtml(data.service)}</span>
         <span class="muted">${escapeHtml(data.env_b)} → ${escapeHtml(data.env_a)}</span>
       </div>` +
      (data.notes && data.notes.length
        ? data.notes.map((n) => `<div class="note">• ${escapeHtml(n)}</div>`).join("")
        : "") +
      `</div>`,
  );

  if (data.pair) {
    pairState = {
      param: { text: data.param_apply ?? "", include: !!data.param_needs_write },
      secret: { text: data.secret_apply ?? "", include: !!data.secret_needs_write },
    };
    renderPair(parts);
    result.innerHTML = parts.join("");
    if (data.param_needs_write || data.secret_needs_write) {
      pairOnEdit();
      const wrap = document.getElementById("script-actions");
      if (wrap) wrap.appendChild(makeCopyBtn());
      const pwrap = document.getElementById("preview-actions");
      if (pwrap) pwrap.appendChild(makePreviewCopyBtn());
      const ewrap = document.getElementById("exec-actions");
      if (ewrap) ewrap.appendChild(makePairExecuteBtn());
    }
    return;
  }

  if (data.status === "equal") {
    result.innerHTML = parts.join("");
    return;
  }

  const showA = data.value_a ?? "";
  const showB = data.value_b ?? "";
  parts.push(
    `<div class="columns">
       <div class="panel">
         <div class="section-title"><strong>Región de Origen — ${escapeHtml(data.env_b)}</strong></div>
         ${preBlock(showB, "No existe en la región de origen")}
       </div>
       <div class="panel">
         <div class="section-title"><strong>Región Destino — ${escapeHtml(data.env_a)}</strong></div>
         ${preBlock(showA, "No existe en la región destino")}
       </div>
     </div>`,
  );

  const hasChanges = Array.isArray(data.changes) && data.changes.length > 0;

  if (hasChanges && data.is_json) {
    isJSON = true;
    rows = data.changes.map((c) => ({
      path: c.path,
      op: c.op,
      old: c.old,
      text:
        c.op === "del"
          ? ""
          : c.new != null && typeof c.new === "object"
          ? JSON.stringify(c.new, null, 2)
          : c.new == null
          ? ""
          : String(c.new),
      include: true,
    }));
    parts.push(renderChangesTable());
    result.innerHTML = parts.join("");
    attachChangeHandlers();
    onEdit();
    const wrap = document.getElementById("script-actions");
    if (wrap) wrap.appendChild(makeCopyBtn());
    const pwrap = document.getElementById("preview-actions");
    if (pwrap) pwrap.appendChild(makePreviewCopyBtn());
    const ewrap = document.getElementById("exec-actions");
    if (ewrap) ewrap.appendChild(makeExecuteBtn("update", () => serializeMerged()));
    return;
  }

  if (hasChanges && !data.is_json) {
    isJSON = false;
    rows = [
      { path: "$", op: "set", old: data.value_a, text: data.value_b ?? "", include: true },
    ];
    parts.push(renderPlainEdit());
    result.innerHTML = parts.join("");
    attachChangeHandlers();
    onEdit();
    const wrap = document.getElementById("script-actions");
    if (wrap) wrap.appendChild(makeCopyBtn());
    const pwrap = document.getElementById("preview-actions");
    if (pwrap) pwrap.appendChild(makePreviewCopyBtn());
    const ewrap = document.getElementById("exec-actions");
    if (ewrap) ewrap.appendChild(makeExecuteBtn("update", () => serializeMerged()));
    return;
  }

  if (data.script && data.status === "different") {
    const patchSection = data.is_json
      ? `<div class="panel" style="margin-top:14px;">
           <div class="section-title"><strong>Valor a aplicar en la región destino (${escapeHtml(data.env_a)})</strong>
           <span class="muted">(solo claves cambiadas)</span></div>
           ${preBlock(data.patch_value, "—")}
         </div>`
      : "";
    const scriptSection = `<div class="panel script-block" style="margin-top:14px;">
        <div class="section-title"><strong>Comando de actualización para la región destino (${escapeHtml(data.env_a)})</strong></div>
        ${preBlock(data.script || "No hay comando que ejecutar.", "—")}
        <div class="actions" style="margin-top:10px;"><span id="script-actions"></span><span id="exec-actions"></span></div>
        <div id="exec-result"></div>
      </div>`;

    parts.push(
      patchSection
        ? `<div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start;">
             ${scriptSection}
             ${patchSection}
           </div>`
        : scriptSection,
    );
    result.innerHTML = parts.join("");

    if (data.script) {
      copyButton(data.script).then((btn) => {
        const wrap = document.getElementById("script-actions");
        if (wrap) wrap.appendChild(btn);
      });
      const ewrap = document.getElementById("exec-actions");
      if (ewrap) {
        ewrap.appendChild(makeExecuteBtn("update", () => data.value_b ?? ""));
      }
    }
    return;
  }

  if (["missing_in_a", "missing_in_b", "none"].includes(data.status)) {
    createTarget = data.status === "missing_in_a" ? "a" : "b";
    const isB = data.status === "missing_in_b";
    const isNone = data.status === "none";
    const suggested =
      data.status === "missing_in_a"
        ? (data.value_b ?? "")
        : data.status === "missing_in_b"
        ? (data.value_a ?? "")
        : "";
    const createRegion = createTarget === "a" ? data.env_a : data.env_b;
    const desc =
      isNone
        ? "No existe en ninguna región — escribí la data para crearlo."
        : data.status === "missing_in_a"
        ? `Falta en la región destino (${data.env_a}) — el valor de origen se puede editar antes de crear.`
        : `Falta en la región de origen (${data.env_b}) — escribí la data para crearlo.`;

    parts.push(
      `<div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; margin-top:14px;">
         <div class="panel">
           <div class="section-title"><strong>Data para crear</strong>
           <span class="muted" style="font-size:12px;">${escapeHtml(desc)}</span></div>
           <textarea id="create-value" class="change-input" rows="6" spellcheck="false">${escapeHtml(suggested)}</textarea>
         </div>
         <div class="panel script-block">
           <div class="section-title"><strong>Comando de creación — ${escapeHtml(createRegion)}</strong></div>
           <pre id="script-pre" class="script-block">Regenerando…</pre>
           <div class="actions" style="margin-top:10px;"><span id="script-actions"></span><span id="exec-actions"></span></div>
           <div id="exec-result"></div>
         </div>
       </div>`,
    );
    result.innerHTML = parts.join("");
    const ta = document.getElementById("create-value");
    ta.addEventListener("input", createOnEdit);
    refreshCreateScript();
    const wrap = document.getElementById("script-actions");
    if (wrap) wrap.appendChild(makeCopyBtn());
    const ewrap = document.getElementById("exec-actions");
    if (ewrap) {
      ewrap.appendChild(makeCreateExecuteBtn());
      if (isB) {
        ewrap.appendChild(
          makeExecuteBtn("delete", () => "", "a", `Solo eliminar en ${data.env_a}`),
        );
      }
    }
    return;
  }

  result.innerHTML = parts.join("");
}

function renderChangesTable() {
  const bodyRows = rows
    .map((r, i) => {
      const opBadge = `<span class="badge ${r.op === "del" ? "missing_in_b" : "different"}">${escapeHtml(r.op)}</span>`;
      const input =
        r.op === "del"
          ? '<span class="muted">Se elimina la clave</span>'
          : `<textarea class="change-input" data-i="${i}" rows="1" spellcheck="false">${escapeHtml(r.text)}</textarea>`;
      return `<tr>
        <td><input type="checkbox" class="change-include" data-i="${i}" ${r.include ? "checked" : ""}></td>
        <td><code>${escapeHtml(r.path)}</code></td>
        <td>${opBadge}</td>
        <td>${formatValue(r.old)}</td>
        <td>${input}</td>
      </tr>`;
    })
    .join("");
  return `<div class="panel">
     <div class="section-title">
       <strong>Cambios</strong>
       <span class="muted">(marcá los que querés llevar a destino y editá los valores a aplicar)</span>
     </div>
     <table>
       <thead><tr><th></th><th>Ruta</th><th>Operación</th><th>Valor actual en la región destino</th><th>Valor a aplicar</th></tr></thead>
       <tbody>${bodyRows}</tbody>
     </table>
   </div>
   <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; margin-top:14px;">
     <div class="panel">
<div class="section-title"><strong>Valor resultante hacia la region destino (${escapeHtml(data.env_a)})</strong></div>
        <pre id="preview-pre" class="script-block"></pre>
        <div class="actions" style="margin-top:10px;"><span id="preview-actions"></span></div>
      </div>
<div class="panel script-block">
        <div class="section-title"><strong>Comando de actualización para la región destino (${escapeHtml(data.env_a)})</strong></div>
        <pre id="script-pre" class="script-block">Regenerando…</pre>
        <div class="actions" style="margin-top:10px;"><span id="script-actions"></span><span id="exec-actions"></span></div>
        <div id="exec-result"></div>
     </div>
   </div>`;
}

function renderPlainEdit() {
  return `<div class="panel">
     <div class="section-title"><strong>Valor a aplicar en la región destino (${escapeHtml(data.env_a)})</strong>
     <span class="muted">(texto plano, no JSON)</span></div>
     <textarea class="change-input" data-i="0" rows="4" spellcheck="false">${escapeHtml(rows[0].text)}</textarea>
   </div>
   <div style="display:grid; grid-template-columns:1fr 1fr; gap:14px; align-items:start; margin-top:14px;">
     <div class="panel">
<div class="section-title"><strong>Valor resultante hacia la region destino</strong></div>
        <pre id="preview-pre" class="script-block"></pre>
        <div class="actions" style="margin-top:10px;"><span id="preview-actions"></span></div>
     </div>
     <div class="panel script-block">
       <div class="section-title"><strong>Comando de actualización para la región destino (${escapeHtml(data.env_a)})</strong></div>
       <pre id="script-pre" class="script-block">Regenerando…</pre>
       <div class="actions" style="margin-top:10px;"><span id="script-actions"></span><span id="exec-actions"></span></div>
       <div id="exec-result"></div>
     </div>
   </div>`;
}

function attachChangeHandlers() {
  document.querySelectorAll(".change-include").forEach((cb) =>
    cb.addEventListener("change", () => {
      rows[+cb.dataset.i].include = cb.checked;
      onEdit();
    }),
  );
  document.querySelectorAll(".change-input").forEach((ta) =>
    ta.addEventListener("input", () => {
      rows[+ta.dataset.i].text = ta.value;
      onEdit();
    }),
  );
}

function formatValue(v) {
  if (v === null || v === undefined) return '<span class="muted">—</span>';
  const text = typeof v === "string" ? v : JSON.stringify(v, null, 2);
  return `<pre style="margin:0; padding:6px 8px; font-size:12px;">${escapeHtml(text)}</pre>`;
}

compareBtn.addEventListener("click", async () => {
  const payload = {
    env_a: envA.value,
    env_b: envB.value,
    service: document.getElementById("service").value,
    name: document.getElementById("name").value.trim(),
    include_deletes: document.getElementById("include-deletes").checked,
    with_secret: document.getElementById("with-secret").checked,
  };

  if (!payload.env_a || !payload.env_b) {
    result.innerHTML = renderError("Seleccioná las dos regiones.");
    return;
  }

  if (!payload.name) {
    result.innerHTML = renderError("Ingresá el nombre del parámetro o secreto.");
    return;
  }

  compareBtn.disabled = true;
  result.innerHTML = `<div class="panel"><span class="spinner"></span>Consultando ${escapeHtml(
    payload.name,
  )} de origen ${payload.env_b} a destino ${payload.env_a}...</div>`;

  try {
    const res = await fetch("/api/params/diff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      result.innerHTML = renderError(data.detail || "Error desconocido");
      if (SESSION_ID) {
        sessionUpdate({
          service: payload.service,
          is_secret: payload.with_secret,
          diff_err: data.detail || String(res.status),
        });
      }
      return;
    }
    render(data);
    if (SESSION_ID) {
      sessionUpdate({
        status: "revisado",
        service: payload.service,
        is_secret: payload.with_secret || !!data.pair,
        diff_json: JSON.stringify(data),
        diff_err: null,
        script: data.script || null,
        preview: data.pair
          ? null
          : (data.patch_value ?? (data.value_b ?? null)),
        notes: (data.notes || []).join("\n"),
      });
    }
  } catch (e) {
    result.innerHTML = renderError("Error de conexión con el backend: " + e.message);
  } finally {
    compareBtn.disabled = false;
  }
});