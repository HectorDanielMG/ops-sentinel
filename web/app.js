const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 2) => value == null ? "—" : Number(value).toFixed(digits);
const ago = (stamp) => {
  if (!stamp) return "Esperando primera comprobación";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(stamp).getTime()) / 1000));
  return seconds < 60 ? `hace ${seconds} s` : `hace ${Math.floor(seconds / 60)} min`;
};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let toastTimer;
function toast(message) { const el = $("toast"); el.textContent = message; el.classList.add("show"); clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("show"), 2600); }

function renderServices(services) {
  $("nav-count").textContent = services.length;
  $("service-list").innerHTML = services.length ? services.map((s, i) => {
    const healthy = s.status === "up", down = s.status === "down", stale = s.status === "stale";
    const state = healthy ? "up" : down ? "down" : stale ? "stale" : "unknown";
    const uptime = s.uptime_24h == null ? "—" : `${fmt(s.uptime_24h, 2)}%`;
    const progress = s.uptime_24h == null ? 0 : Math.min(100, s.uptime_24h / s.slo_target * 100);
    const warning = s.uptime_24h != null && s.uptime_24h < s.slo_target;
    const budget = s.error_budget_remaining_percent;
    const stateLabel = healthy ? "ACTIVO" : down ? "CAÍDO" : stale ? "RETRASADO" : "SIN DATOS";
    return `<div class="service-row"><div class="service-name"><span class="service-symbol">${i % 2 ? "⌘" : "◈"}</span><div class="service-copy"><strong>${esc(s.name)}</strong><small>${esc(new URL(s.url).host)}</small></div></div><span class="status ${state}"><i></i>${stateLabel}</span><div class="availability">${uptime}<small>${s.checks_24h} comprob.</small></div><div class="latency">${s.latency_ms == null ? "—" : fmt(s.latency_ms, 0)} <small>ms</small></div><div class="slo-wrap" title="Presupuesto de error restante en 24 horas"><span class="slo-track"><span class="slo-fill ${warning ? "warn" : ""}" style="display:block;width:${progress}%"></span></span><span class="slo-value">${fmt(s.slo_target, 1)}%</span>${budget == null ? "" : `<small class="budget-value">${fmt(budget, 0)}% restante</small>`}</div></div>`;
  }).join("") : `<div class="empty-state"><span>◈</span>Aún no hay servicios. Agrega un endpoint para iniciar el monitoreo.</div>`;
}

function renderIncidents(incidents) {
  const active = incidents.filter(i => !i.resolved_at);
  $("nav-incidents").textContent = active.length;
  $("incident-list").innerHTML = incidents.length ? incidents.slice(0, 6).map(i => {
    const resolved = Boolean(i.resolved_at);
    return `<div class="incident-item"><span class="event-icon ${resolved ? "resolved" : "open"}">${resolved ? "✓" : "!"}</span><div class="event-copy"><strong>${esc(i.service_name)} ${resolved ? "se recuperó" : "presenta problemas"}</strong><p>${esc(i.message)}</p><time>${new Date(i.opened_at).toLocaleString("es-MX")}${resolved ? ` · Resuelto ${ago(i.resolved_at)}` : ""}</time></div><span class="event-state ${resolved ? "resolved" : "open"}">${resolved ? "RESUELTO" : "EN CURSO"}</span></div>`;
  }).join("") : `<div class="empty-state"><span>✓</span>No hay incidentes. Los servicios funcionan correctamente.</div>`;
  $("incident-caption").innerHTML = active.length ? `<span style="color:var(--red)">●</span> ${active.length} servicio${active.length === 1 ? " requiere" : "s requieren"} atención` : `<span class="green-text">●</span> Todos los sistemas funcionan`;
  $("incident-icon").className = `stat-icon ${active.length ? "" : "green"}`;
}

async function refresh() {
  try {
    const [summary, services, incidents] = await Promise.all([
      fetch("/api/summary").then(r => r.json()), fetch("/api/services").then(r => r.json()), fetch("/api/incidents").then(r => r.json())
    ]);
    $("fleet-uptime").innerHTML = `${fmt(summary.fleet_uptime_24h, 2)}<small>%</small>`;
    $("service-total").textContent = summary.service_count;
    $("incident-total").textContent = summary.active_incidents;
    renderServices(services); renderIncidents(incidents);
    $("updated").textContent = `Actualizado ${new Date().toLocaleTimeString("es-MX", {hour:"2-digit",minute:"2-digit",second:"2-digit"})}`;
  } catch { $("updated").textContent = "Error de conexión"; }
}

$("refresh").addEventListener("click", async () => {
  $("refresh").classList.add("spinning");
  try { await fetch("/api/checks/run", {method:"POST"}); await refresh(); toast("Comprobaciones completadas"); }
  catch { toast("No se pudieron ejecutar las comprobaciones"); }
  finally { $("refresh").classList.remove("spinning"); }
});
$("open-modal").addEventListener("click", () => $("service-modal").showModal());
$("close-modal").addEventListener("click", () => $("service-modal").close());
$("view-incidents").addEventListener("click", () => $("incidents").scrollIntoView({behavior:"smooth",block:"start"}));
$("service-modal").addEventListener("click", event => { if (event.target === $("service-modal")) $("service-modal").close(); });
$("service-form").addEventListener("submit", async event => {
  event.preventDefault(); $("form-error").textContent = "";
  const form = new FormData(event.currentTarget);
  try {
    const response = await fetch("/api/services", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:form.get("name"),url:form.get("url"),slo_target:Number(form.get("slo_target"))})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "No se pudo agregar el servicio");
    event.currentTarget.reset(); $("service-modal").close(); toast("Servicio agregado al monitoreo");
    await fetch("/api/checks/run", {method:"POST"}); await refresh();
  } catch (error) { $("form-error").textContent = error.message; }
});
refresh(); setInterval(refresh, 10000);
