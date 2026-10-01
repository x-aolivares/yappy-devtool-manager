const envSel = document.getElementById("env");
const confirmBox = document.getElementById("confirm");
const confirmEnv = document.getElementById("confirm-env");
const compileBtn = document.getElementById("compile-btn");
const codeInput = document.getElementById("code");
const result = document.getElementById("result");

loadEnvs(envSel).catch((e) => {
  result.innerHTML = renderError("No se pudieron cargar los ambientes: " + e.message);
});

function refreshConfirmLabel() {
  confirmEnv.textContent = envSel.value || "…";
}

envSel.addEventListener("change", refreshConfirmLabel);

function stmtBadge(ok) {
  return `<span class="badge ${ok ? "ok" : "error"}">${ok ? "OK" : "Error"}</span>`;
}

function meta(row) {
  const parts = [`${row.ms} ms`];
  if (row.ok && row.affected !== null && row.affected !== undefined) {
    parts.push(`${row.affected} filas`);
  }
  return parts.join(" · ");
}

function render(data) {
  const parts = [];

  if (data.err_count === 0) {
    parts.push(
      `<div class="ok-box"><strong>Listo.</strong> ${data.ok_count} sentencia${
        data.ok_count === 1 ? "" : "s"
      } ejecutada${data.ok_count === 1 ? "" : "s"} correctamente en ${escapeHtml(data.env)}.</div>`,
    );
  } else {
    parts.push(
      `<div class="error-box"><strong>${data.err_count} sentencia${
        data.err_count === 1 ? "" : "s"
      } fallaron</strong> de ${data.ok_count + data.err_count} en ${escapeHtml(data.env)}.</div>`,
    );
  }

  rows = data.results
    .map(
      (row) => `
      <div class="stmt-row">
        <span>#${row.index}</span>
        ${stmtBadge(row.ok)}
        <div style="flex:1; min-width:0;">
          <pre class="stmt-preview">${escapeHtml(row.sql)}</pre>
          <div class="muted" style="display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;">
            <span>${meta(row)}</span>
            ${row.error ? `<span style="color:var(--err);">${escapeHtml(row.error)}</span>` : ""}
          </div>
        </div>
      </div>`,
    )
    .join("");

  parts.push(`<div class="panel">${rows}</div>`);
  result.innerHTML = parts.join("");
}

compileBtn.addEventListener("click", async () => {
  const typeInput = document.querySelector('input[name="object-type"]:checked');
  const payload = {
    env: envSel.value,
    object_type: typeInput ? typeInput.value : "table",
    schema_name: document.getElementById("schema").value.trim(),
    code: codeInput.value,
  };

  if (!payload.env) {
    result.innerHTML = renderError("Seleccioná el ambiente.");
    return;
  }
  if (!payload.code.trim()) {
    result.innerHTML = renderError("Pegá el código SQL que querés ejecutar.");
    return;
  }
  if (!confirmBox.checked) {
    result.innerHTML = renderError(
      "Confirmá que querés ejecutar esto en " + payload.env + ".",
    );
    return;
  }

  compileBtn.disabled = true;
  result.innerHTML = `<div class="panel"><span class="spinner"></span>Ejecutando en ${escapeHtml(
    payload.env,
  )}...</div>`;

  try {
    const res = await fetch("/api/execute/sql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      result.innerHTML = renderError(data.detail || "Error desconocido");
      return;
    }
    render(data);
  } catch (e) {
    result.innerHTML = renderError("Error de conexión con el backend: " + e.message);
  } finally {
    compileBtn.disabled = false;
  }
});

refreshConfirmLabel();