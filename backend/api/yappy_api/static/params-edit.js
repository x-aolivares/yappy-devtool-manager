const envSel = document.getElementById("env");
const nameInput = document.getElementById("name");
const loadBtn = document.getElementById("load-btn");
const editor = document.getElementById("editor");
const meta = document.getElementById("meta");
const valueInput = document.getElementById("value");
const typeSel = document.getElementById("value-type");
const saveBtn = document.getElementById("save-btn");
const result = document.getElementById("result");

function autoresize(el) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

async function loadEnvs() {
  const res = await fetch("/api/envs");
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
  envSel.innerHTML = (data.environments || []).map(
    (e) => `<option value="${escapeHtml(e.env)}">${escapeHtml(e.env)}</option>`,
  );
  if (!envSel.options.length)
    throw new Error("no se encontraron ambientes");
}

async function readParam() {
  const env = envSel.value;
  const name = nameInput.value.trim();
  if (!name) {
    result.innerHTML = renderError("Ingresá el nombre del parámetro.");
    return;
  }

  loadBtn.disabled = true;
  result.innerHTML = `<div class="panel"><span class="spinner"></span>Leyendo…</div>`;

  try {
    const res = await fetch(`/api/params/get?env=${encodeURIComponent(env)}&name=${encodeURIComponent(name)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);

    valueInput.value = data.value;
    typeSel.value = data.value_type;
    meta.textContent = `${env} — ${data.value_type}`;
    editor.hidden = false;
    result.innerHTML = "";
    autoresize(valueInput);
  } catch (e) {
    editor.hidden = true;
    result.innerHTML = renderError("Error: " + e.message);
  } finally {
    loadBtn.disabled = false;
  }
}

async function saveParam() {
  const env = envSel.value;
  const name = nameInput.value.trim();
  if (!name) {
    result.innerHTML = renderError("Ingresá el nombre del parámetro.");
    return;
  }
  const ok = confirm(
    `¿Guardar ${name} en ${env}? (put-parameter, overwrite)\n` +
      "Cada región usa su profile/región configurados.",
  );
  if (!ok) return;

  saveBtn.disabled = true;
  result.innerHTML = `<div class="panel"><span class="spinner"></span>Guardando…</div>`;

  try {
    const res = await fetch("/api/params/multi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        value: valueInput.value,
        value_type: typeSel.value,
        envs: [env],
        dry_run: false,
        confirm: true,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
    const rows = data.results || [];
    result.innerHTML =
      rows[0] && rows[0].ok
        ? `<div class="ok-box"><strong>Listo.</strong> ${escapeHtml(rows[0].message || "Parámetro guardado.")}</div>`
        : renderError(rows[0]?.error || "No se pudo guardar.");
  } catch (e) {
    result.innerHTML = renderError("Error: " + e.message);
  } finally {
    saveBtn.disabled = false;
  }
}

loadBtn.addEventListener("click", readParam);
saveBtn.addEventListener("click", saveParam);
nameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") readParam();
});
valueInput.addEventListener("input", () => autoresize(valueInput));

loadEnvs().catch((e) => {
  envSel.innerHTML = `<option value="">(error: ${escapeHtml(e.message)})</option>`;
});