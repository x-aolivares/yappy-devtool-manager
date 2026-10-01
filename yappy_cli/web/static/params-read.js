renderRegionControls(document.getElementById("region-controls"));

const envB = document.getElementById("env-b");
const envA = document.getElementById("env-a");
const serviceSel = document.getElementById("service");
const aliasInput = document.getElementById("session-alias");
const readBtn = document.getElementById("read-btn");
const entriesInput = document.getElementById("entries");
const result = document.getElementById("result");

loadEnvs(envB, envA).catch((e) => {
  result.innerHTML = renderError("No se pudieron cargar los ambientes: " + e.message);
});

function collectEntries() {
  const raw = entriesInput.value.trim();
  if (raw.startsWith("[")) {
    let entries;
    try {
      entries = JSON.parse(raw);
    } catch (e) {
      throw new Error(
        "El JSON no es válido: " +
          e.message +
          ' — formato esperado: [{ "key": "/path", "is_secret": false }] o una clave por línea',
      );
    }
    if (!Array.isArray(entries) || entries.length === 0) {
      throw new Error(
        'El JSON debe ser una lista, por ejemplo: [{ "key": "/yappy/dev/rate", "is_secret": false }]',
      );
    }
    return entries;
  }
  const entries = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((key) => ({ key, is_secret: false }));
  if (entries.length === 0) {
    throw new Error(
      'Pegá una clave por línea, por ejemplo: /prod/ecommerce/db/master_url — o un JSON como [{ "key": "/path", "is_secret": false }]',
    );
  }
  return entries;
}

function formatValue(v) {
  if (v === null || v === undefined || v === "") return "";
  try {
    return JSON.stringify(JSON.parse(v), null, 2);
  } catch {
    return v;
  }
}

function diffUrl(r) {
  const params = new URLSearchParams({
    env_b: envB.value,
    env_a: envA.value,
    service: r.service || "ssm",
    name: r.key,
    with_secret: r.is_secret ? "1" : "0",
  });
  return "/params-diff?" + params.toString();
}

function render(data) {
  const total = data.results.length;
  const ok = data.ok_count;

  const banner =
    ok === total
      ? `<div class="ok-box"><strong>Listo.</strong> ${total} valor${
          total === 1 ? "" : "es"
        } leído${total === 1 ? "" : "s"} en ${escapeHtml(data.env)}.</div>`
      : `<div class="error-box"><strong>${data.err_count} entrada${
          data.err_count === 1 ? "" : "s"
        } con error</strong> de ${total} en ${escapeHtml(data.env)}.</div>`;

  const rows = data.results
    .map((r) => {
      const state = r.ok
        ? '<span class="badge ok">OK</span>'
        : '<span class="badge error">Error</span>';
      const service = r.is_secret
        ? '<span class="badge secret">Secreto</span>'
        : '<span class="muted">SSM</span>';
      const value = r.ok ? formatValue(r.value) : r.error || "—";
      return `<tr>
        <td><code>${escapeHtml(r.key)}</code></td>
        <td>${service}</td>
        <td>${state}</td>
        <td>${preBlock(value, "—")}</td>
        <td><a class="diff-link" href="${diffUrl(r)}" title="Comparar en Parámetros">Sincronizar →</a></td>
      </tr>`;
    })
    .join("");

  result.innerHTML =
    banner +
    `<div class="panel">
       <div class="section-title"><strong>Valores en ${escapeHtml(data.env)}</strong></div>
       <table>
         <thead><tr><th>Nombre</th><th>Servicio</th><th>Estado</th><th>Valor</th><th></th></tr></thead>
         <tbody>${rows}</tbody>
       </table>
     </div>`;
}

function sessionAlias() {
  return (aliasInput && aliasInput.value ? aliasInput.value.trim() : "").trim();
}

function ensureSession(entries) {
  const envB_ = envB.value;
  const envA_ = envA.value;
  if (!envA_ || envA_ === envB_) return Promise.resolve();
  const keys = entries.map((e) =>
    typeof e === "string" ? e : e.key || e.name || ""
  );
  const alias = sessionAlias();
  const requestBody = {
    env_a: envA_,
    env_b: envB_,
    service: serviceSel.value,
    keys,
    title: alias,
    alias,
    reuse: true,
  };
  return fetch("/api/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody),
  })
    .then(async (res) => {
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
      return body;
    })
    .then((body) => {
      const sessionHref = `/sessions/${encodeURIComponent(body.id)}`;
      result.insertAdjacentHTML(
        "afterbegin",
        `<div class="ok-box">Sesión de trabajo <strong>${escapeHtml(body.title)}</strong> lista · ` +
          `<a href="${sessionHref}">Abrir en Sesiones →</a></div>`,
      );
      window.location.assign(sessionHref);
    });
}

readBtn.addEventListener("click", async () => {
  const env = envB.value;
  if (!env) {
    result.innerHTML = renderError("Seleccioná la región de origen.");
    return;
  }

  let entries;
  try {
    entries = collectEntries();
  } catch (e) {
    result.innerHTML = renderError(e.message);
    return;
  }

  readBtn.disabled = true;
  result.innerHTML = `<div class="panel"><span class="spinner"></span>Leyendo ${entries.length} entrada${entries.length === 1 ? "" : "s"} en ${escapeHtml(env)}...</div>`;

  try {
    const res = await fetch(
      "/api/params/read?env=" + encodeURIComponent(env),
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(entries),
      },
    );
    const data = await res.json();
    if (!res.ok) {
      result.innerHTML = renderError(data.detail || "Error desconocido");
      return;
    }
    render(data);
    ensureSession(entries).catch(() => {});
  } catch (e) {
    result.innerHTML = renderError("Error de conexión con el backend: " + e.message);
  } finally {
    readBtn.disabled = false;
  }
});