const view = document.getElementById("main-view");

const STATUS_META = {
  pendiente: ["none", "Pendiente"],
  revisado: ["equal", "Revisado"],
  aplicado: ["ok", "Aplicado"],
  saltado: ["missing_in_b", "Saltado"],
};

function statusBadge(status) {
  const [cls, label] = STATUS_META[status] || ["none", status];
  return `<span class="badge ${cls}">${label}</span>`;
}

function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return (
    d.toLocaleDateString("es-AR") + " " + d.toLocaleTimeString("es-AR", {
      hour: "2-digit",
      minute: "2-digit",
    })
  );
}

function progressBar(counts, total) {
  const done = (counts.aplicado || 0) + (counts.saltado || 0);
  const pct = total ? Math.round((done / total) * 100) : 0;
  return `<div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
    <div class="muted">${done} de ${total} cerrados</div>
    <div style="flex:1; min-width:140px; background:var(--panel); border-radius:6px; height:10px; overflow:hidden; border:1px solid var(--border);">
      <div style="width:${pct}%; height:100%; background:var(--ok);"></div>
    </div>
    <div class="muted">${pct}%</div>
  </div>`;
}

function openInDiff(item, session) {
  const params = new URLSearchParams({
    session: session.id,
    env_a: session.env_a,
    env_b: session.env_b,
    service: item.service || session.service || "ssm",
    name: item.name,
    with_secret: item.is_secret ? "1" : "0",
  });
  return `/params-diff?${params.toString()}`;
}

async function setItemStatus(sessionId, name, status) {
  const res = await fetch(
    `/api/sessions/${encodeURIComponent(sessionId)}/items`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, status }),
    },
  );
  const body = await res.json();
  if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
}

function itemDetails(item) {
  const parts = [];
  if (item.diff_err) parts.push(`Error al diferir:\n${item.diff_err}`);
  if (item.notes) parts.push(`${item.notes}`);
  if (item.script) parts.push(`Comando:\n${item.script}`);
  if (item.preview) parts.push(`Valor a aplicar:\n${item.preview}`);
  if (!parts.length) return "";
  const text = parts.join("\n\n");
  return `<details style="margin-top:6px;"><summary class="muted" style="font-size:12px; cursor:pointer;">Ver guardado</summary>
    <pre class="script-block" style="margin-top:6px; overflow:auto;">${escapeHtml(text)}</pre>
  </details>`;
}

function itemActions(item, session) {
  const btns = [];
  if (item.status !== "aplicado") {
    btns.push(
      `<button class="secondary" data-act="aplicado" data-name="${escapeHtml(item.name)}">Marcar aplicado</button>`,
    );
  }
  if (item.status === "pendiente") {
    btns.push(
      `<button class="secondary" data-act="saltado" data-name="${escapeHtml(item.name)}">Saltar</button>`,
    );
  }
  if (item.status === "saltado" || item.status === "aplicado") {
    btns.push(
      `<button class="secondary" data-act="pendiente" data-name="${escapeHtml(item.name)}">Reabrir</button>`,
    );
  }
  return btns.join(" ");
}

function itemsTable(session, items) {
  const rows = items
    .map((item) => {
      const service =
        item.service === "secretsmanager" || item.is_secret
          ? '<span class="badge secret">Secreto</span>'
          : '<span class="muted">SSM</span>';
      return `<tr>
        <td class="muted">${item.position + 1}</td>
        <td><code>${escapeHtml(item.name)}</code></td>
        <td>${service}</td>
        <td>${statusBadge(item.status)}</td>
        <td>
          <a class="primary" style="text-decoration:none; display:inline-block;"
             href="${openInDiff(item, session)}">Abrir en Parámetros →</a>
          ${itemActions(item, session)}
          ${itemDetails(item)}
        </td>
      </tr>`;
    })
    .join("");
  return `<div class="panel">
    <table>
      <thead><tr><th>#</th><th>Nombre</th><th>Servicio</th><th>Estado</th><th>Acciones</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  </div>`;
}

function addSessionItem(sessionId, name, options = {}) {
  const raw = (name || "").trim();
  if (!raw) throw new Error("Ingresá el nombre del parámetro a agregar.");
  return fetch("/api/sessions/" + encodeURIComponent(sessionId) + "/items/create", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: raw,
      service: options.service || "ssm",
      is_secret: Boolean(options.is_secret),
      status: options.status || "pendiente",
    }),
  }).then(async (res) => {
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
    return body;
  });
}

function renderDetail(session, query = "") {
  const total = session.items.length;
  const pending = session.items.filter((i) => i.status === "pendiente");
  const next = pending[0];
  const filterValue = escapeHtml(query);
  view.innerHTML = `
    <a href="/sessions" class="muted" style="text-decoration:none;">← Sesiones</a>
    <h1>${escapeHtml(session.title)}</h1>
    <p class="muted">
      ${escapeHtml(session.env_b)} (origen) → ${escapeHtml(session.env_a)} (destino) ·
      creada ${fmtDate(session.created_at)} · ${total} parámetros
    </p>
    <div class="panel">${progressBar(session.status_counts, total)}</div>
    <div class="panel" style="margin-top:12px; display:grid; gap:8px;">
      <label for="session-filter">Filtrar por nombre</label>
      <input id="session-filter" type="text" value="${filterValue}" placeholder="/prod/api/..." />
    </div>
    <div class="panel" style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap; align-items:end;">
      <div style="flex:1; min-width:220px;">
        <label for="session-new-name">Agregar parámetro a la sesión</label>
        <input id="session-new-name" type="text" placeholder="/prod/new/param" />
      </div>
      <button id="session-add-item" class="secondary">Agregar</button>
    </div>
    ${next ? `<p style="margin-top:12px;"><a class="primary" style="text-decoration:none;" href="${openInDiff(next, session)}">Siguiente pendiente →</a> <span class="muted">(${pending.length} en cola)</span></p>` : ""}
    <div style="margin-top:12px;">${itemsTable(session, session.items)}</div>`;

  const filterInput = document.getElementById("session-filter");
  if (filterInput) {
    filterInput.addEventListener("input", (event) => {
      const value = event.target.value.trim();
      loadDetail(session.id, value);
    });
  }

  const addBtn = document.getElementById("session-add-item");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const nameInput = document.getElementById("session-new-name");
      try {
        await addSessionItem(session.id, nameInput ? nameInput.value : "", { service: session.service || "ssm" });
        await loadDetail(session.id, filterInput ? filterInput.value : "");
      } catch (e) {
        view.insertAdjacentHTML(
          "afterbegin",
          `<div class="error-box">${escapeHtml(e.message)}</div>`,
        );
      }
    });
  }
}

async function loadDetail(sessionId, filterQuery = "") {
  try {
    const url = new URL("/api/sessions/" + encodeURIComponent(sessionId), window.location.origin);
    if (filterQuery && filterQuery.trim()) {
      url.searchParams.set("filter", filterQuery.trim());
    }
    const res = await fetch(url.toString());
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
    const session = body;
    renderDetail(session, filterQuery);

    view.querySelectorAll("button[data-act]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await setItemStatus(sessionId, btn.dataset.name, btn.dataset.act);
          await loadDetail(sessionId, filterQuery);
        } catch (e) {
          btn.disabled = false;
          view.insertAdjacentHTML(
            "afterbegin",
            `<div class="error-box">${escapeHtml(e.message)}</div>`,
          );
        }
      }),
    );
  } catch (e) {
    view.innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
  }
}

function renderList(sessions) {
  const rows = sessions
    .map((s) => {
      const counts = s.status_counts || {};
      return `<div class="panel" style="margin-bottom:12px;">
        <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
          <div style="flex:1; min-width:220px;">
            <div><strong>${escapeHtml(s.title)}</strong></div>
            <div class="muted">${escapeHtml(s.env_b)} → ${escapeHtml(s.env_a)} · ${s.item_count} parámetros · ${fmtDate(s.created_at)}</div>
          </div>
          ${statusBadge("aplicado")} <span class="muted">${counts.aplicado || 0}</span>
          ${statusBadge("revisado")} <span class="muted">${counts.revisado || 0}</span>
          ${statusBadge("pendiente")} <span class="muted">${counts.pendiente || 0}</span>
          ${counts.saltado ? statusBadge("saltado") + ` <span class="muted">${counts.saltado}</span>` : ""}
        </div>
        <div class="actions" style="margin-top:10px;">
          <a class="primary" style="text-decoration:none;" href="/sessions/${escapeHtml(s.id)}">Continuar / Revisar</a>
          <button class="secondary" data-del="${escapeHtml(s.id)}">Eliminar</button>
        </div>
      </div>`;
    })
    .join("");

  view.innerHTML = `
    <h1>Sesiones de parámetros</h1>
    <p class="muted">
      Una sesión es una lista de parámetros que vas a sincronizar entre una región de <strong>origen</strong>
      y una de <strong>destino</strong>. La creás desde <a href="/params-read">Leer Parámetros</a> pegando tu
      lista (o desde tu hoja de cálculo), y después vas iterando ítem por ítem: cada uno se abre en
      <strong>Parámetros</strong> para ver el diff y ejecutar el cambio. Acá queda guardado el progreso de
      todo — sin tocar AWS.
    </p>
    <a class="primary" style="text-decoration:none; display:inline-block; margin-bottom:16px;" href="/params-read">Crear una nueva sesión</a>
    ${sessions.length ? rows : `<div class="panel"><span class="muted">Todavía no hay sesiones.</span></div>`}`;
}

async function loadList() {
  try {
    const res = await fetch("/api/sessions");
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
    renderList(body.sessions || []);

    view.querySelectorAll("button[data-del]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        if (!confirm("¿Eliminar DEFINITIVAMENTE esta sesión y su progreso?\nNo se puede deshacer.")) return;
        btn.disabled = true;
        try {
          const res = await fetch("/api/sessions/" + encodeURIComponent(btn.dataset.del), {
            method: "DELETE",
          });
          const body = await res.json();
          if (!res.ok) throw new Error(body.detail || "HTTP " + res.status);
          await loadList();
        } catch (e) {
          btn.disabled = false;
          view.insertAdjacentHTML(
            "afterbegin",
            `<div class="error-box">${escapeHtml(e.message)}</div>`,
          );
        }
      }),
    );
  } catch (e) {
    view.innerHTML = `<div class="error-box">${escapeHtml(e.message)}</div>`;
  }
}

const parts = location.pathname.split("/").filter(Boolean);
if (parts[0] === "sessions" && parts.length >= 2) {
  loadDetail(parts[1]);
} else {
  loadList();
}