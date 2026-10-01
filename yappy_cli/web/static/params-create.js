const nameInput = document.getElementById("name");
const valueInput = document.getElementById("value");
const typeSel = document.getElementById("value-type");
const secretChk = document.getElementById("create-secret");
const envsWrap = document.getElementById("envs");
const previewBtn = document.getElementById("preview-btn");
const createBtn = document.getElementById("create-btn");
const result = document.getElementById("result");

function withSecret() {
  return secretChk.checked;
}

secretChk.addEventListener("change", () => {
  typeSel.disabled = withSecret();
  if (withSecret()) typeSel.value = "SecureString";
});

async function loadEnvChecks() {
  const res = await fetch("/api/envs");
  const data = await res.json();
  if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
  const envs = data.environments || [];
  if (!envs.length) throw new Error("no se encontraron ambientes");
  envsWrap.innerHTML = envs
    .map(
      (e) =>
        `<label class="env-check">
           <input type="checkbox" value="${escapeHtml(e.env)}">
           <span><strong>${escapeHtml(e.env)}</strong> — ${escapeHtml(
          e.region || "",
        )}<span class="muted"> (${escapeHtml(e.profile || "")})</span></span>
         </label>`,
    )
    .join("");
}

function selectedEnvs() {
  return [...envsWrap.querySelectorAll("input[type=checkbox]:checked")].map(
    (cb) => cb.value,
  );
}

function payload(dryRun) {
  return {
    name: nameInput.value.trim(),
    value: valueInput.value,
    value_type: typeSel.value,
    envs: selectedEnvs(),
    create_secret: withSecret(),
    dry_run: dryRun,
    confirm: !dryRun,
  };
}

function validate() {
  if (!payload(true).name) {
    result.innerHTML = renderError("Ingresá el nombre del parámetro.");
    return null;
  }
  const envs = selectedEnvs();
  if (!envs.length) {
    result.innerHTML = renderError("Marcá al menos una región destino.");
    return null;
  }
  return { name: payload(true).name, envs };
}

function render(results, dryRun) {
  const { name, ok_count, err_count } = { name: payload(true).name, ok_count: results.filter((r) => r.ok).length, err_count: results.filter((r) => !r.ok).length };
  const secret = withSecret();
  const total = results.length;
  const banner =
    err_count === 0
      ? `<div class="ok-box"><strong>Listo.</strong> ${ok_count} región${
          ok_count === 1 ? "" : "es"
        }${dryRun ? " con comando generado" : ""} para ${escapeHtml(name)}${
          secret ? " (secreto + parámetro SSM)" : ""
        }.</div>`
      : `<div class="error-box"><strong>${err_count} región${
          err_count === 1 ? "" : "es"
        } con error</strong> de ${total} para ${escapeHtml(name)}.</div>`;

  const rows = results
    .map((r) => {
      const state = r.ok
        ? '<span class="badge ok">OK</span>'
        : '<span class="badge error">Error</span>';
      const detail = r.ok
        ? r.script
          ? `<div style="display:flex; flex-direction:column; gap:8px;">${preBlock(
              r.script,
              "—",
            )}<span class="copy-wrap"></span></div>`
          : `<div class="note ok">${escapeHtml(r.message || "")}</div>`
        : `<div class="note err">${escapeHtml(r.error || "Error desconocido")}</div>`;
      return `<tr>
        <td><strong>${escapeHtml(r.env)}</strong></td>
        <td>${state}</td>
        <td>${detail}</td>
      </tr>`;
    })
    .join("");

  result.innerHTML =
    banner +
    `<div class="panel">
       <div class="section-title"><strong>${dryRun ? "Comandos por región" : "Resultado de la ejecución"}</strong></div>
       <table>
         <thead><tr><th>Región</th><th>Estado</th><th>${dryRun ? "Comando" : "Detalle"}</th></tr></thead>
         <tbody>${rows}</tbody>
       </table>
     </div>`;

  if (dryRun) {
    result.querySelectorAll(".copy-wrap").forEach((wrap) => {
      const pre = wrap.parentElement && wrap.parentElement.querySelector(".script-block");
      if (!pre) return;
      copyButton(pre.textContent).then((btn) => wrap.appendChild(btn));
    });
  }
}

async function run(dryRun) {
  const check = validate();
  if (!check) return;
  if (!dryRun) {
    const secret = withSecret();
    const ops = secret
      ? "crear/actualizar el secreto en Secrets Manager Y el parámetro SSM (SecureString)"
      : "ejecutar put-parameter";
    const ok = confirm(
      `¿${ops} de ${check.name} en: ${check.envs.join(", ")}?\n` +
        "Cada región usa su profile/región configurados.",
    );
    if (!ok) return;
  }

  const btn = dryRun ? previewBtn : createBtn;
  btn.disabled = true;
  result.innerHTML = `<div class="panel"><span class="spinner"></span>${
    dryRun ? "Generando comandos…" : "Ejecutando…"
  }</div>`;

  try {
    const res = await fetch("/api/params/multi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload(dryRun)),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "HTTP " + res.status);
    render(data.results || [], dryRun);
  } catch (e) {
    result.innerHTML = renderError("Error: " + e.message);
  } finally {
    btn.disabled = false;
  }
}

previewBtn.addEventListener("click", () => run(true));
createBtn.addEventListener("click", () => run(false));

loadEnvChecks().catch((e) => {
  envsWrap.innerHTML = `<span class="muted">No se pudieron cargar los ambientes: ${escapeHtml(e.message)}</span>`;
});