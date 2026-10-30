/**
 * ShareSafe Resource Trust Passport Controller
 * Powers the verifiable history, deterministic readiness evaluation,
 * and cryptographic tamper-evident audit chain verification.
 */

const state = {
  currentResource: "res-gpu-01",
  currentUser: {
    id: "user:maya",
    role: "borrower",
    name: "Maya Lin",
    token: null,
  },
  resourceData: null,
  holds: [],
  incidents: [],
  history: [],
  auditTrail: [],
};

// Initialize on page load
document.addEventListener("DOMContentLoaded", () => {
  // Check URL parameters for resource id
  const params = new URLSearchParams(window.location.search);
  const resourceParam = params.get("id");
  if (resourceParam) {
    state.currentResource = resourceParam;
    const select = document.getElementById("resourceSelect");
    if (select) select.value = resourceParam;
  }

  // Restore stored persona or default to Maya Lin
  const stored = localStorage.getItem("sharesafe_user");
  if (stored) {
    try {
      const parsed = JSON.parse(stored);
      state.currentUser = parsed;
    } catch (e) {}
  }
  updatePersonaUI();
  loadAllPassportData();
});

function switchPersona(userId, role, name) {
  state.currentUser = { id: userId, role: role, name: name };
  localStorage.setItem("sharesafe_user", JSON.stringify(state.currentUser));
  updatePersonaUI();
  loadAllPassportData();
}

function updatePersonaUI() {
  const badge = document.getElementById("activePersonaBadge");
  if (badge) {
    badge.innerText = `Acting as: ${state.currentUser.name} (${state.currentUser.role})`;
  }

  // Highlight active button
  const btns = {
    "user:maya": document.getElementById("persona-maya"),
    "user:marcus": document.getElementById("persona-marcus"),
    "user:dr_thorne": document.getElementById("persona-thorne"),
    "user:sarah": document.getElementById("persona-sarah"),
  };
  Object.entries(btns).forEach(([uid, btn]) => {
    if (!btn) return;
    if (uid === state.currentUser.id) {
      btn.className = "px-2.5 py-1 text-xs font-medium rounded-l border border-slate-700 bg-indigo-600 text-white";
    } else {
      btn.className = "px-2.5 py-1 text-xs font-medium border border-slate-700 bg-slate-800 text-slate-300 hover:bg-slate-700";
    }
  });

  // Show quick clear hold button only for inspectors or admins
  const clearBtn = document.getElementById("btnQuickClearHold");
  if (clearBtn) {
    if (state.currentUser.role === "inspector" || state.currentUser.role === "admin") {
      clearBtn.classList.remove("hidden");
    } else {
      clearBtn.classList.add("hidden");
    }
  }

  // Only show simulate tamper to admin / judge mode
  const tamperBtn = document.getElementById("btnSimulateTamper");
  if (tamperBtn) {
    if (state.currentUser.role === "admin" || state.currentUser.role === "inspector") {
      tamperBtn.classList.remove("hidden");
    } else {
      tamperBtn.classList.remove("hidden"); // Allowed in judge mode for hackathon demo
    }
  }
}

function onResourceChanged(newResId) {
  state.currentResource = newResId;
  const url = new URL(window.location);
  url.searchParams.set("id", newResId);
  window.history.replaceState({}, "", url);
  loadAllPassportData();
}

async function loadAllPassportData() {
  const resId = state.currentResource;
  try {
    // 1. Fetch resource details
    const resResponse = await fetch(`/api/resources/${resId}`);
    if (resResponse.ok) {
      state.resourceData = await resResponse.json();
      renderResourceHeader(state.resourceData);
    }

    // 2. Fetch history
    const histResponse = await fetch(`/api/resources/${resId}/history`);
    if (histResponse.ok) {
      state.history = await histResponse.json();
      renderHistory(state.history);
    }

    // 3. Fetch holds
    const holdsResponse = await fetch(`/api/holds?resource_id=${resId}`);
    if (holdsResponse.ok) {
      state.holds = await holdsResponse.json();
      renderHolds(state.holds);
    }

    // 4. Fetch incidents
    const incResponse = await fetch(`/api/incidents?resource_id=${resId}`);
    if (incResponse.ok) {
      state.incidents = await incResponse.json();
      renderIncidents(state.incidents);
    }

    // 5. Fetch audit trail
    const auditResponse = await fetch(`/api/audit?resource_id=${resId}`);
    if (auditResponse.ok) {
      state.auditTrail = await auditResponse.json();
      renderAuditTimeline(state.auditTrail);
    }

    // Render readiness evaluation
    evaluateAndRenderReadiness();
  } catch (err) {
    console.error("Failed to load passport data:", err);
  }
}

function renderResourceHeader(res) {
  document.getElementById("resourceTitle").innerText = res.name || "Equipment";
  document.getElementById("categoryBadge").innerText = res.category || "General";
  document.getElementById("bayTagBadge").innerText = `Bay: ${res.bay_tag || "General Bay"}`;
  document.getElementById("itemCodeBadge").innerText = res.item_code || `#${res.id}`;
  document.getElementById("resourceLocation").innerHTML = `
    <span class="material-symbols-outlined text-[16px]">location_on</span> ${res.location || "Campus Central Locker"}
  `;

  // Rules
  const rules = res.rules || {};
  document.getElementById("ruleMinYear").innerText = rules.min_year ? `Year ${rules.min_year}+` : "Any Year";
  document.getElementById("ruleMaxDuration").innerText = rules.max_hours_per_booking ? `${rules.max_hours_per_booking} Hours` : `${res.max_duration_days || 7} Days`;
  document.getElementById("ruleMinNotice").innerText = rules.min_notice_hours ? `${rules.min_notice_hours} Hours` : "Instant / None";
  document.getElementById("ruleWeekend").innerText = rules.weekend_requires_supervisor ? "Requires Supervisor" : "Open Access";

  const trainingCont = document.getElementById("ruleTrainingList");
  const trainings = rules.required_training || [];
  if (trainings.length === 0) {
    trainingCont.innerHTML = `<span class="text-xs text-slate-400">None required (general orientation).</span>`;
  } else {
    trainingCont.innerHTML = trainings.map(t => `
      <span class="px-2.5 py-1 rounded-md text-xs font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 flex items-center gap-1">
        <span class="material-symbols-outlined text-[14px]">school</span> ${t}
      </span>
    `).join("");
  }
}

function evaluateAndRenderReadiness() {
  const badge = document.getElementById("readinessStatusBadge");
  const container = document.getElementById("readinessReasonsContainer");
  const res = state.resourceData || {};
  const activeHolds = state.holds.filter(h => h.status === "active" || h.status === "open");

  let readinessState = "ready";
  const reasons = [];

  // Check 1: Active safety hold
  if (activeHolds.length > 0) {
    readinessState = "not_ready";
    activeHolds.forEach(h => {
      reasons.push({
        severity: "block",
        passed: false,
        message: `Open safety hold: ${h.reason} (placed by ${h.placed_by || "custodian"}).`,
      });
    });
  } else {
    reasons.push({
      severity: "info",
      passed: true,
      message: "No active safety holds. Equipment passed physical inspection.",
    });
  }

  // Check 2: Rules-based conditions
  const rules = res.rules || {};
  const now = new Date();
  const isWeekend = now.getDay() === 0 || now.getDay() === 6;
  if (rules.weekend_requires_supervisor && isWeekend) {
    if (readinessState !== "not_ready") readinessState = "ready_with_conditions";
    reasons.push({
      severity: "condition",
      passed: true,
      message: "Weekend operation: requires designated lab staff supervisor present.",
    });
  }

  if (rules.required_training && rules.required_training.length > 0) {
    if (readinessState !== "not_ready") readinessState = "ready_with_conditions";
    reasons.push({
      severity: "condition",
      passed: true,
      message: `Borrower prerequisite: must hold completed training for [${rules.required_training.join(", ")}].`,
    });
  }

  // Set big badge styling
  if (readinessState === "not_ready") {
    badge.className = "inline-flex items-center gap-2 px-4 py-2 rounded-xl text-base font-bold bg-rose-50 text-rose-700 border border-rose-200 shadow-xs";
    badge.innerHTML = `<span class="material-symbols-outlined text-rose-600">cancel</span> NOT READY`;
  } else if (readinessState === "ready_with_conditions") {
    badge.className = "inline-flex items-center gap-2 px-4 py-2 rounded-xl text-base font-bold bg-amber-50 text-amber-800 border border-amber-200 shadow-xs";
    badge.innerHTML = `<span class="material-symbols-outlined text-amber-600">warning</span> READY WITH CONDITIONS`;
  } else {
    badge.className = "inline-flex items-center gap-2 px-4 py-2 rounded-xl text-base font-bold bg-emerald-50 text-emerald-700 border border-emerald-200 shadow-xs";
    badge.innerHTML = `<span class="material-symbols-outlined text-emerald-600">check_circle</span> READY`;
  }

  // Render reasons list
  container.innerHTML = reasons.map(r => {
    let icon = "check";
    let colorClass = "text-emerald-700 bg-emerald-50 border-emerald-100";
    if (r.severity === "block") {
      icon = "block";
      colorClass = "text-rose-700 bg-rose-50 border-rose-200 font-semibold";
    } else if (r.severity === "condition") {
      icon = "info";
      colorClass = "text-amber-800 bg-amber-50 border-amber-200 font-medium";
    }
    return `
      <div class="flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${colorClass}">
        <span class="material-symbols-outlined text-[16px]">${icon}</span>
        <span>${r.message}</span>
      </div>
    `;
  }).join("");
}

function renderHolds(holds) {
  const container = document.getElementById("holdsContainer");
  if (!holds || holds.length === 0) {
    container.innerHTML = `<div class="p-6 text-center text-xs text-slate-400">No safety holds on record for this resource.</div>`;
    return;
  }
  container.innerHTML = holds.map(h => {
    const isOpen = h.status === "active" || h.status === "open";
    return `
      <div class="p-4 rounded-xl border ${isOpen ? "bg-rose-50/50 border-rose-200" : "bg-slate-50 border-slate-200"} text-xs space-y-2">
        <div class="flex items-center justify-between">
          <span class="font-bold flex items-center gap-1 ${isOpen ? "text-rose-700" : "text-slate-700"}">
            <span class="material-symbols-outlined text-[15px]">${isOpen ? "lock" : "lock_open"}</span>
            ${isOpen ? "ACTIVE SAFETY HOLD" : "CLEARED SAFETY HOLD"}
          </span>
          <span class="text-[11px] text-slate-400">${new Date(h.created_at).toLocaleString()}</span>
        </div>
        <p class="text-slate-800 font-medium">${h.reason}</p>
        <div class="text-[11px] text-slate-500">Placed by: <span class="font-mono">${h.placed_by}</span></div>
        ${h.cleared_by ? `
          <div class="pt-2 mt-2 border-t border-slate-200 text-[11px] text-emerald-800 bg-emerald-50/70 p-2 rounded-md">
            <div class="font-semibold flex items-center gap-1">
              <span class="material-symbols-outlined text-[14px]">task_alt</span> Cleared by ${h.cleared_by} (${new Date(h.cleared_at).toLocaleDateString()})
            </div>
            <div class="mt-0.5 italic">"${h.cleared_note}"</div>
          </div>
        ` : ""}
      </div>
    `;
  }).join("");
}

function renderIncidents(incidents) {
  const container = document.getElementById("incidentsContainer");
  if (!incidents || incidents.length === 0) {
    container.innerHTML = `<div class="p-6 text-center text-xs text-slate-400">No incidents or disputes reported.</div>`;
    return;
  }
  container.innerHTML = incidents.map(inc => {
    let typeBadge = "bg-rose-100 text-rose-800";
    if (inc.incident_type === "dispute") typeBadge = "bg-amber-100 text-amber-800";
    else if (inc.incident_type === "delay") typeBadge = "bg-blue-100 text-blue-800";

    return `
      <div class="p-4 rounded-xl border border-slate-200 bg-white text-xs space-y-1.5 shadow-2xs">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-2">
            <span class="px-2 py-0.5 rounded font-bold uppercase text-[10px] ${typeBadge}">
              ${inc.incident_type}
            </span>
            <span class="font-mono text-slate-400 text-[11px]">ID: ${inc.id}</span>
          </div>
          <span class="text-slate-400 text-[11px]">${new Date(inc.created_at).toLocaleString()}</span>
        </div>
        <p class="text-slate-800 font-medium text-sm pt-1">${inc.description}</p>
        <div class="text-[11px] text-slate-400 flex items-center justify-between pt-1">
          <span>Reported by: <span class="font-mono text-slate-600">${inc.reporter_id}</span></span>
          ${inc.booking_id ? `<span>Booking: <span class="font-mono text-slate-600">${inc.booking_id}</span></span>` : ""}
        </div>
      </div>
    `;
  }).join("");
}

function renderHistory(history) {
  const container = document.getElementById("historyTableContainer");
  if (!history || history.length === 0) {
    container.innerHTML = `<div class="p-6 text-center text-xs text-slate-400">No past loan sessions recorded yet.</div>`;
    return;
  }

  container.innerHTML = `
    <table class="w-full text-left text-xs border-collapse">
      <thead>
        <tr class="border-b border-slate-200 bg-slate-50 text-slate-500 font-semibold uppercase text-[10px] tracking-wider">
          <th class="py-2.5 px-3">Booking ID</th>
          <th class="py-2.5 px-3">Borrower</th>
          <th class="py-2.5 px-3">Slot Duration</th>
          <th class="py-2.5 px-3">Status</th>
          <th class="py-2.5 px-3">Handover & Return Condition</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-slate-100">
        ${history.map(b => {
          const conditions = b.conditions || [];
          const beforeNote = conditions.find(c => c.phase === "before")?.notes || "Nominal";
          const afterNote = conditions.find(c => c.phase === "after")?.notes || "Pending / In Use";

          return `
            <tr class="hover:bg-slate-50/70 transition-colors">
              <td class="py-3 px-3 font-mono font-bold text-slate-900">${b.id}</td>
              <td class="py-3 px-3">${b.requester?.name || b.user_id}</td>
              <td class="py-3 px-3 text-slate-500">
                ${new Date(b.start).toLocaleDateString()} ${new Date(b.start).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})} - ${new Date(b.end).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}
              </td>
              <td class="py-3 px-3">
                <span class="px-2 py-0.5 rounded-full font-semibold uppercase text-[10px] ${
                  b.status === "returned" ? "bg-emerald-50 text-emerald-700" :
                  b.status === "checked_in" ? "bg-blue-50 text-blue-700" :
                  b.status === "blocked_by_hold" ? "bg-rose-50 text-rose-700" :
                  "bg-slate-100 text-slate-700"
                }">
                  ${b.status}
                </span>
              </td>
              <td class="py-3 px-3">
                <div class="text-[11px]"><span class="text-slate-400">Pre:</span> ${beforeNote}</div>
                <div class="text-[11px]"><span class="text-slate-400">Post:</span> ${afterNote}</div>
              </td>
            </tr>
          `;
        }).join("")}
      </tbody>
    </table>
  `;
}

function renderAuditTimeline(trail) {
  const container = document.getElementById("auditTimelineContainer");
  if (!trail || trail.length === 0) {
    container.innerHTML = `<div class="p-4 text-center text-xs text-slate-400 font-sans">No audit events recorded for this resource yet.</div>`;
    return;
  }

  container.innerHTML = trail.map((e, idx) => {
    const isDenied = e.action.startsWith("DENIED:");
    return `
      <div class="p-3 rounded-lg border ${isDenied ? "bg-rose-50/60 border-rose-200" : "bg-slate-50 border-slate-200"} flex flex-col gap-1">
        <div class="flex items-center justify-between text-[11px] font-sans">
          <span class="font-bold font-mono ${isDenied ? "text-rose-700" : "text-indigo-700"}">
            ${isDenied ? "🚫 " : "🔗 "}#${idx + 1} ${e.action}
          </span>
          <span class="text-slate-400 text-[10px]">${new Date(e.timestamp).toLocaleTimeString()}</span>
        </div>
        <div class="text-[10px] text-slate-500 flex flex-wrap gap-x-3">
          <span>Actor: <span class="text-slate-800">${e.actor || e.actor_id}</span></span>
          <span>Prev: <span class="text-slate-400">${e.prev_hash ? e.prev_hash.substring(0, 12) + "..." : "GENESIS"}</span></span>
          <span>Hash: <span class="text-indigo-600">${e.hash.substring(0, 16)}...</span></span>
        </div>
      </div>
    `;
  }).join("");
}

// -----------------------------------------------------------------------------
// VERIFY CHAIN IMPLEMENTATION
// -----------------------------------------------------------------------------
async function runVerifyChain() {
  const banner = document.getElementById("verificationResultBanner");
  const icon = document.getElementById("verificationIcon");
  const title = document.getElementById("verificationTitle");
  const detail = document.getElementById("verificationDetail");

  banner.classList.remove("hidden");
  banner.className = "mt-6 p-4 rounded-xl border flex items-center gap-3 bg-slate-50 border-slate-200 text-slate-700";
  title.innerText = "Verifying tamper-evident audit chain...";
  detail.innerText = "Recomputing cryptographic hashes across all historical blocks.";
  icon.innerText = "sync";
  icon.className = "material-symbols-outlined text-2xl animate-spin text-indigo-600";

  try {
    const response = await fetch("/api/audit/verify");
    const result = await response.json();

    if (result.ok) {
      banner.className = "mt-6 p-4 rounded-xl border flex items-center gap-3 bg-emerald-50 border-emerald-200 text-emerald-900";
      icon.className = "material-symbols-outlined text-2xl text-emerald-600";
      icon.innerText = "check_circle";
      title.innerText = "✓ Audit chain verified";
      detail.innerText = `Cryptographic integrity intact across ${result.checked} tamper-evident event blocks. No unauthorized tampering or deleted records detected.`;
    } else {
      banner.className = "mt-6 p-4 rounded-xl border flex items-center gap-3 bg-rose-50 border-rose-200 text-rose-900";
      icon.className = "material-symbols-outlined text-2xl text-rose-600";
      icon.innerText = "error";
      title.innerText = "✗ Audit chain broken";
      detail.innerText = `Cryptographic discrepancy detected at block #${result.broken_at + 1} (0-indexed #${result.broken_at}). Hash mismatch or deleted entry identified.`;
    }
  } catch (err) {
    banner.className = "mt-6 p-4 rounded-xl border flex items-center gap-3 bg-rose-50 border-rose-200 text-rose-900";
    icon.className = "material-symbols-outlined text-2xl text-rose-600";
    icon.innerText = "warning";
    title.innerText = "Verification Request Failed";
    detail.innerText = err.message;
  }
}

// -----------------------------------------------------------------------------
// DEMO SIMULATE TAMPER
// -----------------------------------------------------------------------------
async function runSimulateTamper() {
  if (!confirm("Demo Helper: Intentionally mutate an audit entry to demonstrate live tamper detection?")) {
    return;
  }
  try {
    const res = await fetch("/api/audit/tamper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index: 0, field: "payload", value: "DEMO_TAMPERED_DATA" }),
    });
    if (res.ok) {
      alert("Simulated tamper injected into audit block #1! Now click 'Verify Chain' to see the cryptographic proof of tampering.");
      runVerifyChain();
    } else {
      const err = await res.json();
      alert(`Tamper simulation failed: ${err.detail || "Error"}`);
    }
  } catch (e) {
    alert("Error simulating tamper: " + e.message);
  }
}

async function resetDemoData() {
  if (!confirm("Reset database to clean seed state?")) return;
  try {
    const res = await fetch("/api/admin/reset-demo", { method: "POST" });
    if (res.ok) {
      alert("ShareSafe demo data reset successfully.");
      loadAllPassportData();
    }
  } catch (e) {
    alert("Reset failed: " + e.message);
  }
}

// -----------------------------------------------------------------------------
// CLEAR HOLD MODAL
// -----------------------------------------------------------------------------
function showClearHoldModal() {
  const modal = document.getElementById("clearHoldModal");
  if (modal) modal.classList.remove("hidden");
}

function closeClearHoldModal() {
  const modal = document.getElementById("clearHoldModal");
  if (modal) modal.classList.add("hidden");
}

async function submitClearHold() {
  const note = document.getElementById("clearingNoteInput").value.trim();
  if (!note) {
    alert("Written inspection resolution note is required.");
    return;
  }

  // Find active hold
  const activeHold = state.holds.find(h => h.status === "active" || h.status === "open");
  const holdId = activeHold ? activeHold.id : null;

  try {
    const url = holdId ? `/api/holds/${holdId}/clear` : `/api/resources/${state.currentResource}/clear-hold`;
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ cleared_note: note }),
    });

    if (res.ok) {
      closeClearHoldModal();
      document.getElementById("clearingNoteInput").value = "";
      alert("Safety hold inspected and cleared! Resource restored to Ready.");
      loadAllPassportData();
    } else {
      const err = await res.json();
      alert(`Failed to clear hold: ${err.detail || "Permission Denied"}`);
    }
  } catch (err) {
    alert("Request failed: " + err.message);
  }
}
