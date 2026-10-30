/**
 * ShareSafe — Campus Equipment & Space Sharing Controller
 * Implements the sleek Tailwind UI with full interactive flows:
 * - Dynamic Catalog with Live Filtering, Sorting, and Search
 * - Interactive Reservation Drawer with Real API Booking
 * - Custodian & Owner Request Review (Approve / Reject)
 * - Smart Locker Contactless Dispatch Kiosk (Check-Out / Return)
 * - Chain-of-Custody Audit Log (History)
 * - Real-Time User Switching (Student <-> Lab Custodian)
 */

// ============================================================
// State
// ============================================================
const state = {
  users: [],
  activeUserId: 'u1',
  resources: [],
  requests: [],
  activeView: 'catalog',
  categoryFilter: '',
  availabilityFilter: '',
  trainingFilter: '',
  searchQuery: '',
  sortKey: 'featured',
  requestsTab: 'pending',
  bookmarkedIds: new Set(JSON.parse(localStorage.getItem('ss_bookmarks') || '[]')),
  selectedResourceId: null
};

// ============================================================
// API Helpers
// ============================================================
const API = '/api';

async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.activeUserId) {
    headers['X-User-Id'] = state.activeUserId;
  }
  const token = localStorage.getItem(`ss_token_${state.activeUserId}`);
  if (token && !headers['Authorization']) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const res = await fetch(API + path, { ...options, headers });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || 'API error');
  }
  return res.json();
}

const get = (path) => apiFetch(path);
const post = (path, body) => apiFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const patch = (path, body = {}) => apiFetch(path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

// Judge Mode & Admin Helpers
window.setDemoUser = async function(userId) {
  state.activeUserId = userId;
  localStorage.setItem('ss_active_user', userId);
  try {
    const auth = await post(`/auth/demo-login/${userId}`, {});
    if (auth && auth.token) {
      localStorage.setItem(`ss_token_${userId}`, auth.token);
    }
  } catch (e) {
    console.warn('Demo login token fallback:', e);
  }
  const select = document.getElementById('userSelectDropdown');
  if (select) select.value = userId;
  updateUserProfileDisplay();
  await refreshData();
  const u = getActiveUser();
  showToast(`Switched persona to: ${u ? u.name : userId} (${u ? u.role : ''})`, 'info');
};

window.resetDemo = async function() {
  if (!confirm('Reset all demo resources, bookings, and audit records to seed state?')) return;
  try {
    await post('/admin/reset-demo', {});
    showToast('Demo state successfully reset to clean seed!', 'success');
    await refreshData();
  } catch (e) {
    showToast('Reset failed: ' + e.message, 'error');
  }
};

// ============================================================
// Toast Notifications
// ============================================================
function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  if (!container) return;

  const icons = {
    success: 'verified',
    error: 'error',
    info: 'info',
    warning: 'warning'
  };

  const bgStyles = {
    success: 'bg-emerald-800 text-white border-emerald-700',
    error: 'bg-rose-900 text-white border-rose-800',
    info: 'bg-primary-container text-white border-slate-700',
    warning: 'bg-amber-800 text-white border-amber-700'
  };

  const el = document.createElement('div');
  el.className = `ss-toast pointer-events-auto flex items-center gap-2.5 px-4 py-3 rounded-xl border shadow-xl text-xs font-medium max-w-sm ${bgStyles[type] || bgStyles.success}`;
  el.innerHTML = `
    <span class="material-symbols-outlined text-[18px] flex-shrink-0">${icons[type] || 'check_circle'}</span>
    <span class="flex-1 leading-snug">${message}</span>
  `;

  container.appendChild(el);

  setTimeout(() => {
    el.classList.add('removing');
    el.addEventListener('animationend', () => el.remove());
  }, 4000);
}

// ============================================================
// Initialization
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
  await loadUsers();
  await refreshData();
  setupEventListeners();
});

// ============================================================
// Users & Profile Switcher
// ============================================================
async function loadUsers() {
  try {
    state.users = await get('/users');
    const select = document.getElementById('userSelectDropdown');
    if (!select) return;

    select.innerHTML = state.users.map(u => `
      <option value="${u.id}">${u.name}</option>
    `).join('');

    const saved = localStorage.getItem('ss_active_user');
    if (saved && state.users.some(u => u.id === saved)) {
      state.activeUserId = saved;
    } else if (state.users.length > 0) {
      state.activeUserId = state.users[0].id;
    }

    select.value = state.activeUserId;
    updateUserProfileDisplay();

    select.addEventListener('change', () => {
      state.activeUserId = select.value;
      localStorage.setItem('ss_active_user', state.activeUserId);
      updateUserProfileDisplay();
      refreshData();
      showToast(`Switched active user to ${getActiveUser()?.name}`, 'info');
    });
  } catch (err) {
    console.error('Failed to load users:', err);
  }
}

function getActiveUser() {
  return state.users.find(u => u.id === state.activeUserId) || state.users[0] || null;
}

function updateUserProfileDisplay() {
  const user = getActiveUser();
  if (!user) return;

  const avatarImg = document.getElementById('userAvatarImg');
  const roleLabel = document.getElementById('userSubRole');
  const slotIndicator = document.getElementById('loanSlotIndicator');
  const requestsRoleLabel = document.getElementById('requestsRoleLabel');

  if (avatarImg) {
    if (user.avatar) {
      avatarImg.src = user.avatar;
    } else {
      // Default generated avatar initial
      avatarImg.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(user.name)}&background=131b2e&color=fff&bold=true`;
    }
  }

  if (roleLabel) {
    roleLabel.textContent = user.title || (user.role === 'owner' ? 'Lab Custodian' : 'Student Scholar');
  }

  if (requestsRoleLabel) {
    requestsRoleLabel.textContent = `Viewing as ${user.name} (${user.title || user.role})`;
  }

  // Calculate active loans for this user
  if (slotIndicator) {
    const activeBorrows = state.requests.filter(
      r => r.requester_id === state.activeUserId && (r.status === 'approved' || r.status === 'checked_out')
    ).length;
    slotIndicator.textContent = `${activeBorrows} of 2 Loan Slots Active`;
  }
}

// ============================================================
// Data Refresh
// ============================================================
async function refreshData() {
  try {
    const [resources, requests] = await Promise.all([
      get('/resources'),
      get('/requests')
    ]);

    state.resources = resources;
    state.requests = requests;

    updateHeaderStats();
    renderCurrentView();
  } catch (err) {
    console.error('Error refreshing data:', err);
    showToast('Failed to load latest campus data: ' + err.message, 'error');
  }
}

function updateHeaderStats() {
  const total = state.resources.length;
  const available = state.resources.filter(r => r.status === 'available').length;
  const pendingRequests = state.requests.filter(r => r.status === 'pending').length;

  const statUnits = document.getElementById('statUnitsActive');
  const statInstant = document.getElementById('statInstantPickup');
  const badge = document.getElementById('requestsCountBadge');

  if (statUnits) statUnits.textContent = `${total} Active Units`;
  if (statInstant) statInstant.textContent = `${available} Available for Instant Pickup`;

  if (badge) {
    badge.textContent = pendingRequests;
    badge.classList.toggle('hidden', pendingRequests === 0);
  }

  // Update Category Count Badges
  const catAll = document.getElementById('catCountAll');
  const catLab = document.getElementById('catCountLab');
  const catAv = document.getElementById('catCountAv');
  const catTools = document.getElementById('catCountTools');

  if (catAll) catAll.textContent = total;
  if (catLab) catLab.textContent = `(${state.resources.filter(r => r.category === 'lab-equipment').length})`;
  if (catAv) catAv.textContent = `(${state.resources.filter(r => r.category === 'av-equipment').length})`;
  if (catTools) catTools.textContent = `(${state.resources.filter(r => r.category === 'tools').length})`;

  updateUserProfileDisplay();
}

function renderCurrentView() {
  if (state.activeView === 'catalog') renderCatalog();
  else if (state.activeView === 'requests') renderRequests();
  else if (state.activeView === 'kiosk') renderKiosk();
  else if (state.activeView === 'history') renderHistory();
}

// ============================================================
// View Navigation
// ============================================================
function setView(viewName) {
  state.activeView = viewName;
  document.querySelectorAll('.view-panel').forEach(panel => {
    panel.classList.add('hidden');
    panel.classList.remove('block');
  });

  const activePanel = document.getElementById(`view-${viewName}`);
  if (activePanel) {
    activePanel.classList.remove('hidden');
    activePanel.classList.add('block');
  }

  // Update nav buttons
  document.querySelectorAll('.nav-btn').forEach(btn => {
    const isTarget = btn.dataset.view === viewName;
    if (isTarget) {
      btn.classList.add('text-secondary', 'border-secondary', 'font-semibold');
      btn.classList.remove('text-on-surface-variant', 'border-transparent');
    } else {
      btn.classList.remove('text-secondary', 'border-secondary', 'font-semibold');
      btn.classList.add('text-on-surface-variant', 'border-transparent');
    }
  });

  renderCurrentView();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ============================================================
// VIEW 1: CATALOG RENDERING
// ============================================================
function renderCatalog() {
  const grid = document.getElementById('catalogGrid');
  if (!grid) return;

  let items = [...state.resources];

  // Category filter
  if (state.categoryFilter) {
    items = items.filter(r => r.category === state.categoryFilter);
  }

  // Availability filter
  if (state.availabilityFilter) {
    items = items.filter(r => r.status === state.availabilityFilter);
  }

  // Training prerequisite filter
  if (state.trainingFilter === 'none') {
    items = items.filter(r => !r.training_required);
  } else if (state.trainingFilter === 'required') {
    items = items.filter(r => r.training_required);
  }

  // Search filter
  if (state.searchQuery.trim()) {
    const q = state.searchQuery.toLowerCase();
    items = items.filter(r =>
      r.name.toLowerCase().includes(q) ||
      r.location.toLowerCase().includes(q) ||
      r.description.toLowerCase().includes(q) ||
      (r.item_code && r.item_code.toLowerCase().includes(q))
    );
  }

  // Sort
  if (state.sortKey === 'name') {
    items.sort((a, b) => a.name.localeCompare(b.name));
  } else if (state.sortKey === 'newest') {
    items.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
  }

  if (items.length === 0) {
    grid.innerHTML = `
      <div class="col-span-full py-16 text-center bg-surface-container-lowest rounded-2xl border border-outline-variant/30 p-8">
        <span class="material-symbols-outlined text-5xl text-outline mb-2">search_off</span>
        <h3 class="text-base font-bold text-on-surface">No equipment matching criteria</h3>
        <p class="text-xs text-on-surface-variant mt-1">Try resetting your active filters or clear your search term.</p>
        <button onclick="resetAllFilters()" class="mt-4 px-4 py-2 rounded-lg bg-surface-container text-xs font-semibold hover:bg-surface-container-high transition-colors">
          Reset All Filters
        </button>
      </div>
    `;
    return;
  }

  grid.innerHTML = items.map(r => renderCatalogCard(r)).join('');

  // Attach dynamic event handlers
  grid.querySelectorAll('[data-reserve-id]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.getAttribute('data-reserve-id');
      const item = state.resources.find(r => r.id === id);
      if (item) openReservationDrawer(item);
    });
  });

  grid.querySelectorAll('[data-bookmark-id]').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const id = btn.getAttribute('data-bookmark-id');
      toggleBookmark(id);
    });
  });
}

function renderCatalogCard(r) {
  const isBookmarked = state.bookmarkedIds.has(r.id);
  const isAvailable = r.status === 'available';
  const isRequested = r.status === 'requested';
  const isCheckedOut = r.status === 'checked_out';

  // Badge configs
  let badgeHtml = '';
  if (isAvailable) {
    badgeHtml = `
      <span class="absolute top-3 right-3 px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 text-xs font-semibold flex items-center gap-1 shadow-sm backdrop-blur-sm">
        <span class="w-1.5 h-1.5 rounded-full bg-emerald-600 animate-pulse"></span> Available Now
      </span>
    `;
  } else if (isRequested) {
    badgeHtml = `
      <span class="absolute top-3 right-3 px-2.5 py-1 rounded-full bg-amber-50 text-amber-800 border border-amber-200 text-xs font-semibold flex items-center gap-1 shadow-sm backdrop-blur-sm">
        <span class="w-1.5 h-1.5 rounded-full bg-amber-500"></span> Due Today 5:00 PM
      </span>
    `;
  } else {
    badgeHtml = `
      <span class="absolute top-3 right-3 px-2.5 py-1 rounded-full bg-slate-100 text-slate-700 border border-slate-300 text-xs font-semibold flex items-center gap-1 shadow-sm backdrop-blur-sm">
        <span class="material-symbols-outlined text-[14px]">lock_clock</span> In Use / Bench Active
      </span>
    `;
  }

  const bayTag = r.bay_tag || 'Locker Ready';
  const itemCode = r.item_code || `ID: ${r.id.toUpperCase()}`;

  // Action Button config
  let actionButtonHtml = '';
  if (isAvailable) {
    actionButtonHtml = `
      <button data-reserve-id="${r.id}" type="button" class="flex-1 py-2 px-4 rounded-lg bg-primary hover:bg-slate-800 text-on-primary font-label-md text-sm text-center font-medium transition-colors shadow-xs flex items-center justify-center gap-1.5 cursor-pointer">
        <span class="material-symbols-outlined text-[18px]">calendar_add_on</span> Reserve Item
      </button>
    `;
  } else if (isRequested) {
    actionButtonHtml = `
      <button data-reserve-id="${r.id}" type="button" class="flex-1 py-2 px-4 rounded-lg bg-surface-container-high hover:bg-surface-container-highest text-on-surface font-label-md text-sm text-center font-medium transition-colors border border-outline-variant/30 flex items-center justify-center gap-1.5 cursor-pointer">
        <span class="material-symbols-outlined text-[18px]">update</span> Join Waitlist / Reserve Ahead
      </button>
    `;
  } else {
    actionButtonHtml = `
      <button data-reserve-id="${r.id}" type="button" class="flex-1 py-2 px-4 rounded-lg bg-surface-container text-on-surface hover:bg-surface-container-high font-label-md text-sm text-center font-medium transition-colors border border-outline-variant/30 flex items-center justify-center gap-1.5 cursor-pointer">
        <span class="material-symbols-outlined text-[18px]">event_seat</span> Queue Return Alert
      </button>
    `;
  }

  const defaultImg = 'https://lh3.googleusercontent.com/aida-public/AB6AXuAmxXq8J2i-qcGn3Sfi6PeE40BZ0lNAUEzBduCuuegSwW-O5EZqiB6udmX3Q2I-QYIshdM3VCYbA3UNRb3znwref2sRX9aa3cnmRFNEARnSytkupL85csltSChTVHMJtWW56d3pW09HWzWCVTeasjroHqTTA88WngfQr4_DTNeMeFBGMIuO-MXrF1TKwMTcAoXEQWG1UzVxJ8IpQ8i-auH-qyUSOHGVkoJL2Yd7gjg';
  const imgUrl = r.image_url || defaultImg;

  return `
    <article class="bg-surface-container-lowest rounded-xl border border-outline-variant/40 overflow-hidden flex flex-col shadow-[0_1px_4px_rgba(15,23,42,0.04)] hover:shadow-md transition-shadow group">
      <div class="relative h-48 w-full bg-surface-container overflow-hidden">
        <img alt="${r.name}" class="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300" src="${imgUrl}"/>
        ${badgeHtml}
        <span class="absolute bottom-3 left-3 px-2 py-0.5 rounded bg-black/75 text-white text-[11px] font-mono backdrop-blur-sm">
          ${bayTag}
        </span>
      </div>

      <div class="p-5 flex-1 flex flex-col justify-between">
        <div class="space-y-2">
          <div class="flex items-center justify-between text-xs text-on-surface-variant">
            <span>${r.location}</span>
            <span class="font-mono text-[11px] text-outline">${itemCode}</span>
          </div>

          <h3 class="font-headline-md text-[18px] font-bold text-on-surface">${r.name}</h3>
          <p class="text-xs text-on-surface-variant line-clamp-2">${r.description}</p>

          <!-- Specs & Requirements Pills -->
          <div class="pt-2 flex flex-wrap gap-1.5">
            ${r.training_required ? `
              <span class="px-2 py-0.5 rounded text-[11px] font-medium bg-amber-50 text-amber-800 border border-amber-200">
                ${r.training_description || 'Req: Lab Safety Lvl 1'}
              </span>
            ` : `
              <span class="px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-50 text-emerald-800 border border-emerald-200">
                No Prerequisite
              </span>
            `}
            <span class="px-2 py-0.5 rounded text-[11px] font-medium bg-surface-container-low text-on-surface-variant">
              ${r.max_duration_days ? `${r.max_duration_days * 24}h max loan` : '48h max loan'}
            </span>
            ${r.usage_rules ? `
              <span class="px-2 py-0.5 rounded text-[11px] font-medium bg-surface-container-low text-on-surface-variant truncate max-w-[180px]" title="${r.usage_rules}">
                ${r.usage_rules}
              </span>
            ` : ''}
          </div>
        </div>

        <div class="pt-5 mt-4 border-t border-outline-variant/30 flex items-center gap-2">
          ${actionButtonHtml}
          <button data-bookmark-id="${r.id}" type="button" class="p-2 rounded-lg border border-outline-variant/40 ${isBookmarked ? 'text-secondary bg-secondary-fixed/30 border-secondary' : 'text-on-surface-variant hover:text-on-surface hover:bg-surface-container-low'} transition-colors cursor-pointer" title="Bookmark resource">
            <span class="material-symbols-outlined text-[18px]">${isBookmarked ? 'bookmark' : 'bookmark_border'}</span>
          </button>
        </div>
      </div>
    </article>
  `;
}

function toggleBookmark(id) {
  if (state.bookmarkedIds.has(id)) {
    state.bookmarkedIds.delete(id);
    showToast('Removed from saved bookmarks', 'info');
  } else {
    state.bookmarkedIds.add(id);
    showToast('Saved to your equipment bookmarks!', 'success');
  }
  localStorage.setItem('ss_bookmarks', JSON.stringify([...state.bookmarkedIds]));
  renderCatalog();
}

function resetAllFilters() {
  state.categoryFilter = '';
  state.availabilityFilter = '';
  state.trainingFilter = '';
  state.searchQuery = '';
  state.sortKey = 'featured';

  const searchInput = document.getElementById('headerSearchInput');
  const filterAv = document.getElementById('filterAvailability');
  const filterTr = document.getElementById('filterTraining');
  const sortDrop = document.getElementById('sortDropdown');

  if (searchInput) searchInput.value = '';
  if (filterAv) filterAv.value = '';
  if (filterTr) filterTr.value = '';
  if (sortDrop) sortDrop.value = 'featured';

  document.querySelectorAll('.cat-pill').forEach(btn => {
    btn.classList.toggle('bg-primary', btn.dataset.cat === '');
    btn.classList.toggle('text-on-primary', btn.dataset.cat === '');
    btn.classList.toggle('bg-surface-container-lowest', btn.dataset.cat !== '');
    btn.classList.toggle('text-on-surface', btn.dataset.cat !== '');
  });

  renderCatalog();
}

// ============================================================
// RESERVATION SLIDE-OUT DRAWER
// ============================================================
function openReservationDrawer(item) {
  state.selectedResourceId = item.id;
  const backdrop = document.getElementById('reservationDrawerBackdrop');
  const drawer = document.getElementById('reservationDrawerContent');

  const nameEl = document.getElementById('drawerItemName');
  const locEl = document.getElementById('drawerItemLocation');
  const imgEl = document.getElementById('drawerItemImage');
  const resIdEl = document.getElementById('drawerResourceId');
  const clearanceTag = document.getElementById('drawerClearanceTag');
  const submitBtn = document.getElementById('submitBookingBtn');

  if (nameEl) nameEl.textContent = item.name;
  if (locEl) locEl.textContent = `${item.location} • ${item.bay_tag || 'Locker Ready'}`;
  if (imgEl && item.image_url) imgEl.src = item.image_url;
  if (resIdEl) resIdEl.value = item.id;

  const user = getActiveUser();
  if (clearanceTag && user) {
    clearanceTag.textContent = `Clearance Verified for ${user.name}`;
  }

  // Pre-fill dates
  const now = new Date();
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  const startInput = document.getElementById('drawerStartDate');
  const endInput = document.getElementById('drawerEndDate');

  if (startInput) startInput.value = now.toISOString().slice(0, 16);
  if (endInput) endInput.value = tomorrow.toISOString().slice(0, 16);

  if (submitBtn) {
    submitBtn.disabled = false;
    submitBtn.className = "flex-1 py-2.5 px-4 rounded-lg bg-primary hover:bg-slate-800 text-on-primary text-xs font-bold transition-all shadow-xs flex items-center justify-center gap-1.5 cursor-pointer";
    submitBtn.innerHTML = `<span class="material-symbols-outlined text-[18px]">verified</span> Submit Reservation Request`;
  }

  // Animate open
  backdrop.classList.remove('opacity-0', 'pointer-events-none');
  backdrop.classList.add('opacity-100');
  drawer.classList.remove('translate-x-full');
  drawer.classList.add('translate-x-0');
}

function closeReservationDrawer() {
  const backdrop = document.getElementById('reservationDrawerBackdrop');
  const drawer = document.getElementById('reservationDrawerContent');

  if (drawer) {
    drawer.classList.remove('translate-x-0');
    drawer.classList.add('translate-x-full');
  }
  if (backdrop) {
    backdrop.classList.remove('opacity-100');
    backdrop.classList.add('opacity-0', 'pointer-events-none');
  }
}

async function handleReservationSubmit() {
  const submitBtn = document.getElementById('submitBookingBtn');
  const resourceId = document.getElementById('drawerResourceId')?.value;
  const justification = document.getElementById('drawerProjectJustification')?.value;
  const startDate = document.getElementById('drawerStartDate')?.value || new Date().toISOString();
  const endDate = document.getElementById('drawerEndDate')?.value || new Date(Date.now() + 86400000).toISOString();
  const safetyAck = document.getElementById('drawerSafetyCert')?.checked;

  if (!resourceId) return;

  if (!safetyAck) {
    showToast('Please check and certify the Lab Safety Briefing before reserving.', 'warning');
    return;
  }

  try {
    submitBtn.disabled = true;
    submitBtn.innerHTML = `
      <span class="material-symbols-outlined text-[18px] animate-spin">refresh</span>
      Transmitting to Lab Custodian...
    `;

    const payload = {
      resource_id: resourceId,
      requester_id: state.activeUserId,
      message: justification || 'Academic course capstone research project.',
      purpose: 'Course Capstone Project',
      start_date: startDate,
      end_date: endDate,
      training_acknowledged: true
    };

    const newReq = await post('/requests', payload);

    submitBtn.classList.remove('bg-primary');
    submitBtn.classList.add('bg-emerald-700', 'text-white');
    submitBtn.innerHTML = `
      <span class="material-symbols-outlined text-[18px]">verified</span>
      Reservation Confirmed #${newReq.id.slice(0, 8)}
    `;

    showToast(`Request submitted! Lab Manager Marcus Vance has received Maya Lin's reservation request.`, 'success');

    setTimeout(() => {
      closeReservationDrawer();
      refreshData();
    }, 900);
  } catch (err) {
    console.error('Reservation error:', err);
    submitBtn.disabled = false;
    submitBtn.innerHTML = `<span class="material-symbols-outlined text-[18px]">verified</span> Submit Reservation Request`;
    showToast('Failed to reserve: ' + err.message, 'error');
  }
}

// ============================================================
// VIEW 2: REVIEW REQUESTS (Owner & Custodian Queue)
// ============================================================
function renderRequests() {
  const container = document.getElementById('requestsListContainer');
  const badgePending = document.getElementById('badgePendingCount');
  const badgeActive = document.getElementById('badgeActiveCount');

  if (!container) return;

  const pending = state.requests.filter(r => r.status === 'pending' || r.status === 'requested');
  const active = state.requests.filter(r => r.status === 'approved' || r.status === 'checked_out' || r.status === 'checked_in');

  if (badgePending) badgePending.textContent = pending.length;
  if (badgeActive) badgeActive.textContent = active.length;

  let filtered = [];
  if (state.requestsTab === 'pending') {
    filtered = pending;
  } else if (state.requestsTab === 'active') {
    filtered = active;
  } else {
    filtered = state.requests;
  }

  if (filtered.length === 0) {
    container.innerHTML = `
      <div class="py-16 text-center bg-surface-container-lowest rounded-2xl border border-outline-variant/30 p-8">
        <span class="material-symbols-outlined text-5xl text-outline mb-2">task_alt</span>
        <h3 class="text-base font-bold text-on-surface">Queue is clear</h3>
        <p class="text-xs text-on-surface-variant mt-1">There are no requests matching the selected "${state.requestsTab}" filter.</p>
      </div>
    `;
    return;
  }

  container.innerHTML = filtered.map(req => renderRequestCard(req)).join('');

  // Attach action button handlers
  container.querySelectorAll('[data-approve-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.getAttribute('data-approve-id');
      await approveRequest(id, btn);
    });
  });

  container.querySelectorAll('[data-reject-id]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const id = btn.getAttribute('data-reject-id');
      await rejectRequest(id, btn);
    });
  });
}

function renderRequestCard(req) {
  const isPending = req.status === 'pending' || req.status === 'requested';
  const isApproved = req.status === 'approved';
  const isCheckedOut = req.status === 'checked_out' || req.status === 'checked_in';
  const isReturned = req.status === 'returned';

  const defaultImg = 'https://lh3.googleusercontent.com/aida-public/AB6AXuAmxXq8J2i-qcGn3Sfi6PeE40BZ0lNAUEzBduCuuegSwW-O5EZqiB6udmX3Q2I-QYIshdM3VCYbA3UNRb3znwref2sRX9aa3cnmRFNEARnSytkupL85csltSChTVHMJtWW56d3pW09HWzWCVTeasjroHqTTA88WngfQr4_DTNeMeFBGMIuO-MXrF1TKwMTcAoXEQWG1UzVxJ8IpQ8i-auH-qyUSOHGVkoJL2Yd7gjg';
  const imgUrl = req.resource_image || defaultImg;

  let statusBadge = '';
  if (isPending) {
    statusBadge = `<span class="px-2.5 py-1 rounded-full bg-amber-50 text-amber-800 border border-amber-200 text-xs font-semibold">Pending Authorization</span>`;
  } else if (isApproved) {
    statusBadge = `<span class="px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-800 border border-emerald-200 text-xs font-semibold">Approved • Awaiting Pickup</span>`;
  } else if (isCheckedOut) {
    statusBadge = `<span class="px-2.5 py-1 rounded-full bg-blue-50 text-secondary border border-blue-200 text-xs font-semibold">Checked Out</span>`;
  } else {
    statusBadge = `<span class="px-2.5 py-1 rounded-full bg-slate-100 text-slate-700 border border-slate-300 text-xs font-semibold">${req.status.toUpperCase()}</span>`;
  }

  let traceHtml = '';
  if (req.decision_trace && req.decision_trace.rule_results) {
    const rules = req.decision_trace.rule_results.map(r => `
      <div class="flex items-center gap-1.5 text-[11px] ${r.passed ? 'text-emerald-700' : 'text-rose-700 font-semibold'}">
        <span class="material-symbols-outlined text-[14px]">${r.passed ? 'check_circle' : 'cancel'}</span>
        <span>${r.message}</span>
      </div>
    `).join('');
    traceHtml = `
      <div class="mt-2 p-2.5 rounded-xl bg-surface-container-low border border-outline-variant/30 text-xs space-y-1">
        <div class="flex items-center justify-between text-[11px] font-bold text-on-surface">
          <span>🧠 Explainable Decision Trace</span>
          <span class="font-mono uppercase text-[10px] px-1.5 py-0.5 rounded ${req.decision_trace.verdict.includes('approve') ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}">
            ${req.decision_trace.verdict}
          </span>
        </div>
        ${rules}
        ${req.decision_trace.explanation ? `<p class="text-[11px] text-on-surface-variant italic pt-0.5">${req.decision_trace.explanation}</p>` : ''}
      </div>
    `;
  }

  return `
    <div class="bg-surface-container-lowest rounded-2xl border border-outline-variant/30 p-5 shadow-xs flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
      <div class="flex items-start gap-4 flex-1">
        <img alt="${req.resource_name}" class="w-20 h-20 rounded-xl object-cover bg-surface-container flex-shrink-0" src="${imgUrl}"/>
        <div class="space-y-1 flex-1">
          <div class="flex items-center gap-2">
            <h4 class="text-sm font-bold text-on-surface">${req.resource_name}</h4>
            ${statusBadge}
          </div>
          <p class="text-xs text-on-surface-variant font-medium">
            Requested by <strong class="text-on-surface font-semibold">${req.requester_name}</strong> (${req.requester_title || 'Student'})
          </p>
          <p class="text-xs text-on-surface-variant bg-surface-container-low px-2.5 py-1 rounded-lg inline-block">
            <strong>Justification:</strong> ${req.message || 'Capstone project experimentation'}
          </p>
          <div class="text-[11px] text-outline flex items-center gap-2 pt-0.5">
            <span>Window: ${new Date(req.start_date).toLocaleDateString()} – ${new Date(req.end_date).toLocaleDateString()}</span>
            <span>•</span>
            <span class="font-mono">Req ID: #${req.id.slice(0, 8)}</span>
          </div>
          ${traceHtml}
        </div>
      </div>

      <div class="flex items-center gap-2 self-stretch md:self-auto justify-end">
        ${isPending ? `
          <button data-reject-id="${req.id}" class="px-3.5 py-2 rounded-lg border border-outline-variant/40 text-on-surface-variant hover:text-rose-700 hover:border-rose-300 hover:bg-rose-50 text-xs font-semibold transition-colors cursor-pointer">
            Decline
          </button>
          <button data-approve-id="${req.id}" class="px-4 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-800 text-white text-xs font-bold transition-all shadow-xs flex items-center gap-1.5 cursor-pointer">
            <span class="material-symbols-outlined text-[16px]">check_circle</span> Approve Reservation
          </button>
        ` : isApproved ? `
          <span class="text-xs text-emerald-800 font-semibold flex items-center gap-1">
            <span class="material-symbols-outlined text-[16px]">lock_open</span> Ready at Locker
          </span>
        ` : `
          <span class="text-xs text-on-surface-variant font-medium">Completed</span>
        `}
      </div>
    </div>
  `;
}

async function approveRequest(requestId, btn) {
  try {
    btn.disabled = true;
    btn.innerHTML = `<span class="material-symbols-outlined text-[16px] animate-spin">refresh</span> Authorizing...`;
    await patch(`/requests/${requestId}/approve`);
    showToast('Reservation approved! Locker code generated for student.', 'success');
    await refreshData();
  } catch (err) {
    showToast('Failed to approve: ' + err.message, 'error');
    btn.disabled = false;
  }
}

async function rejectRequest(requestId, btn) {
  try {
    btn.disabled = true;
    await patch(`/requests/${requestId}/reject`);
    showToast('Reservation declined. Resource returned to active pool.', 'info');
    await refreshData();
  } catch (err) {
    showToast('Failed to decline: ' + err.message, 'error');
    btn.disabled = false;
  }
}

// ============================================================
// VIEW 3: CHECK-OUT KIOSK
// ============================================================
function renderKiosk() {
  const pickupList = document.getElementById('kioskPickupList');
  const returnList = document.getElementById('kioskReturnList');

  if (!pickupList || !returnList) return;

  // Filter approved requests for pickup
  const approved = state.requests.filter(r => r.status === 'approved');
  // Filter checked_out/checked_in requests for return
  const checkedOut = state.requests.filter(r => r.status === 'checked_out' || r.status === 'checked_in');

  if (approved.length === 0) {
    pickupList.innerHTML = `
      <div class="p-4 rounded-xl bg-surface-container-low border border-dashed border-outline-variant/40 text-center text-xs text-on-surface-variant">
        No approved items waiting for pickup right now.
      </div>
    `;
  } else {
    pickupList.innerHTML = approved.map(req => `
      <div class="p-4 rounded-xl border border-outline-variant/40 bg-surface-container-lowest flex items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-lg bg-emerald-100 text-emerald-800 flex items-center justify-center font-bold text-xs flex-shrink-0">
            <span class="material-symbols-outlined">qr_code</span>
          </div>
          <div>
            <h5 class="text-xs font-bold text-on-surface">${req.resource_name}</h5>
            <p class="text-[11px] text-on-surface-variant">${req.requester_name} • ${req.bay_tag || 'Locker Station A'}</p>
          </div>
        </div>
        <button onclick="kioskCheckout('${req.id}')" class="px-4 py-2 rounded-lg bg-primary hover:bg-slate-800 text-on-primary text-xs font-bold transition-all shadow-xs flex items-center gap-1.5 cursor-pointer">
          <span class="material-symbols-outlined text-[16px]">lock_open</span> Unlock Locker &amp; Check Out
        </button>
      </div>
    `).join('');
  }

  if (checkedOut.length === 0) {
    returnList.innerHTML = `
      <div class="p-4 rounded-xl bg-surface-container-low border border-dashed border-outline-variant/40 text-center text-xs text-on-surface-variant">
        No equipment currently checked out by active scholars.
      </div>
    `;
  } else {
    returnList.innerHTML = checkedOut.map(req => `
      <div class="p-4 rounded-xl border border-outline-variant/40 bg-surface-container-lowest flex items-center justify-between gap-4">
        <div class="flex items-center gap-3">
          <div class="w-10 h-10 rounded-lg bg-blue-100 text-secondary flex items-center justify-center font-bold text-xs flex-shrink-0">
            <span class="material-symbols-outlined">key</span>
          </div>
          <div>
            <h5 class="text-xs font-bold text-on-surface">${req.resource_name}</h5>
            <p class="text-[11px] text-on-surface-variant">In Possession of: ${req.requester_name}</p>
          </div>
        </div>
        <button onclick="kioskReturn('${req.id}')" class="px-4 py-2 rounded-lg bg-emerald-700 hover:bg-emerald-800 text-white text-xs font-bold transition-all shadow-xs flex items-center gap-1.5 cursor-pointer">
          <span class="material-symbols-outlined text-[16px]">assignment_return</span> Return &amp; Lock Bay
        </button>
      </div>
    `).join('');
  }
}

window.kioskCheckout = async function(requestId) {
  try {
    const conditionNote = prompt('Confirm checkout initial inspection condition:', 'Clean, undamaged, verified operational.');
    if (conditionNote === null) return;
    await patch(`/requests/${requestId}/checkout`, { condition_note: conditionNote });
    showToast('Locker door unlatched! Equipment dispatched into borrower custody.', 'success');
    await refreshData();
  } catch (err) {
    showToast('Checkout failed: ' + err.message, 'error');
  }
};

window.kioskReturn = async function(requestId) {
  try {
    const conditionNote = prompt('Confirm return inspection condition (type "fan fault" to test incident hold):', 'Returned in clean, calibrated operational condition.');
    if (conditionNote === null) return;

    let incidentType = null;
    let incidentDescription = null;
    const lower = conditionNote.toLowerCase();
    if (lower.includes('fault') || lower.includes('damage') || lower.includes('broken') || lower.includes('fan')) {
      incidentType = 'fault';
      incidentDescription = conditionNote;
    }

    await patch(`/requests/${requestId}/return`, {
      condition_note: conditionNote,
      incident_type: incidentType,
      incident_description: incidentDescription
    });
    if (incidentType) {
      showToast('⚠️ Fault reported! Precautionary safety hold automatically placed by Custodian Agent.', 'warning');
    } else {
      showToast('Equipment safely verified and locked into locker. Custodianship restored!', 'success');
    }
    await refreshData();
  } catch (err) {
    showToast('Return failed: ' + err.message, 'error');
  }
};

// ============================================================
// VIEW 4: LOAN HISTORY
// ============================================================
function renderHistory() {
  const container = document.getElementById('historyTableContainer');
  const countLabel = document.getElementById('historyRecordCount');
  if (!container) return;

  const logs = [...state.requests].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

  if (countLabel) countLabel.textContent = `${logs.length} Total Audit Records`;

  if (logs.length === 0) {
    container.innerHTML = `
      <div class="p-8 text-center text-xs text-on-surface-variant">No transaction history recorded yet.</div>
    `;
    return;
  }

  container.innerHTML = logs.map(item => `
    <div class="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-surface-container flex items-center justify-center text-on-surface-variant">
          <span class="material-symbols-outlined text-[18px]">receipt_long</span>
        </div>
        <div>
          <span class="font-bold text-on-surface block">${item.resource_name}</span>
          <span class="text-on-surface-variant text-[11px]">
            Borrower: ${item.requester_name} • Custodian: ${item.owner_name}
          </span>
        </div>
      </div>
      <div class="flex items-center gap-3 self-end sm:self-center">
        <span class="text-[11px] text-outline">${new Date(item.created_at).toLocaleString()}</span>
        <span class="px-2 py-0.5 rounded text-[11px] font-semibold ${item.status === 'returned' ? 'bg-emerald-100 text-emerald-800' : item.status === 'checked_out' ? 'bg-blue-100 text-blue-800' : 'bg-slate-100 text-slate-800'}">
          ${item.status.toUpperCase()}
        </span>
      </div>
    </div>
  `).join('');
}

// ============================================================
// LIST NEW RESOURCE MODAL
// ============================================================
function openNewResourceModal() {
  const modal = document.getElementById('newResourceModal');
  if (modal) {
    modal.classList.remove('opacity-0', 'pointer-events-none');
    modal.classList.add('opacity-100');
  }
}

function closeNewResourceModal() {
  const modal = document.getElementById('newResourceModal');
  if (modal) {
    modal.classList.remove('opacity-100');
    modal.classList.add('opacity-0', 'pointer-events-none');
  }
}

async function handleNewResourceSubmit(e) {
  e.preventDefault();
  const name = document.getElementById('newResName')?.value;
  const category = document.getElementById('newResCategory')?.value;
  const bayTag = document.getElementById('newResBayTag')?.value || 'Bay Ready';
  const location = document.getElementById('newResLocation')?.value;
  const imageUrl = document.getElementById('newResImageUrl')?.value || null;
  const description = document.getElementById('newResDesc')?.value;
  const trainingReq = document.getElementById('newResTrainingReq')?.checked;

  try {
    await post('/resources', {
      name,
      category,
      location,
      description,
      owner_id: state.activeUserId,
      image_url: imageUrl,
      bay_tag: bayTag,
      training_required: trainingReq,
      training_description: trainingReq ? 'Req: Lab Safety Clearance' : 'No Prerequisite',
      usage_rules: '48h max loan • Clean after session',
      max_duration_days: 2
    });

    showToast(`Published "${name}" to campus equipment catalog!`, 'success');
    closeNewResourceModal();
    document.getElementById('newResourceForm')?.reset();
    await refreshData();
  } catch (err) {
    showToast('Failed to create resource: ' + err.message, 'error');
  }
}

// ============================================================
// Event Listeners Binding
// ============================================================
function setupEventListeners() {
  // Brand logo click -> go to catalog
  document.getElementById('brandLogoBtn')?.addEventListener('click', (e) => {
    e.preventDefault();
    setView('catalog');
  });

  // Nav Tabs
  document.querySelectorAll('.nav-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const view = btn.getAttribute('data-view');
      if (view) setView(view);
    });
  });

  // Category filter pills
  document.querySelectorAll('.cat-pill').forEach(btn => {
    btn.addEventListener('click', () => {
      const cat = btn.getAttribute('data-cat') || '';
      state.categoryFilter = cat;

      document.querySelectorAll('.cat-pill').forEach(b => {
        const isSelected = (b.getAttribute('data-cat') || '') === cat;
        b.classList.toggle('bg-primary', isSelected);
        b.classList.toggle('text-on-primary', isSelected);
        b.classList.toggle('bg-surface-container-lowest', !isSelected);
        b.classList.toggle('text-on-surface', !isSelected);
      });

      renderCatalog();
    });
  });

  // Dropdown filters
  document.getElementById('filterAvailability')?.addEventListener('change', (e) => {
    state.availabilityFilter = e.target.value;
    renderCatalog();
  });

  document.getElementById('filterTraining')?.addEventListener('change', (e) => {
    state.trainingFilter = e.target.value;
    renderCatalog();
  });

  document.getElementById('sortDropdown')?.addEventListener('change', (e) => {
    state.sortKey = e.target.value;
    renderCatalog();
  });

  document.getElementById('resetFiltersBtn')?.addEventListener('click', resetAllFilters);

  // Search input
  const searchInput = document.getElementById('headerSearchInput');
  if (searchInput) {
    searchInput.addEventListener('input', (e) => {
      state.searchQuery = e.target.value;
      if (state.activeView !== 'catalog') setView('catalog');
      renderCatalog();
    });

    // Keyboard shortcut Cmd+K or Ctrl+K
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        searchInput.focus();
      }
      if (e.key === 'Escape') {
        closeReservationDrawer();
        closeNewResourceModal();
      }
    });
  }

  // Reservation Drawer Controls
  document.getElementById('closeDrawerBtn')?.addEventListener('click', closeReservationDrawer);
  document.getElementById('cancelDrawerBtn')?.addEventListener('click', closeReservationDrawer);
  document.getElementById('reservationDrawerBackdrop')?.addEventListener('click', (e) => {
    if (e.target.id === 'reservationDrawerBackdrop') closeReservationDrawer();
  });
  document.getElementById('submitBookingBtn')?.addEventListener('click', handleReservationSubmit);

  // Review Requests Tabs
  const reqTabPending = document.getElementById('reqTabPending');
  const reqTabActive = document.getElementById('reqTabActive');
  const reqTabAll = document.getElementById('reqTabAll');

  [reqTabPending, reqTabActive, reqTabAll].forEach(tab => {
    if (!tab) return;
    tab.addEventListener('click', () => {
      [reqTabPending, reqTabActive, reqTabAll].forEach(t => {
        t.className = "px-4 py-2 rounded-lg bg-surface-container hover:bg-surface-container-high text-on-surface text-xs font-semibold transition-all flex items-center gap-2 cursor-pointer";
      });
      tab.className = "px-4 py-2 rounded-lg bg-primary text-on-primary text-xs font-bold transition-all shadow-xs flex items-center gap-2 cursor-pointer";

      if (tab === reqTabPending) state.requestsTab = 'pending';
      else if (tab === reqTabActive) state.requestsTab = 'active';
      else state.requestsTab = 'all';

      renderRequests();
    });
  });

  // New Resource Modal
  document.getElementById('openNewResourceModalBtn')?.addEventListener('click', openNewResourceModal);
  document.getElementById('closeNewResourceModalBtn')?.addEventListener('click', closeNewResourceModal);
  document.getElementById('cancelNewResourceBtn')?.addEventListener('click', closeNewResourceModal);
  document.getElementById('newResourceForm')?.addEventListener('submit', handleNewResourceSubmit);
}
