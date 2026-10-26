/* ============================================================
   TripAI — Frontend Application Logic
   Connects to the Python WSGI backend at /plan, /trips, /health
   ============================================================ */

'use strict';

// ─── Config ─────────────────────────────────────────────────
const API_BASE = '';   // same origin (served alongside the backend)
const AUTH_TOKEN = ''; // set this if API_AUTH_TOKEN is configured in .env

const EXAMPLE_BRIEFS = [
  '3 days in Pune, budget 3000 per day, heritage and food, relaxed pace',
  '3 days in Mumbai, budget 4000 per day, views and food, moderate pace',
  '2 days in Delhi, budget 2500 per day, heritage and shopping',
  '3 days in Goa, budget 3500 per day, nature and nightlife',
  '3 days in Jaipur, budget 3000 per day, heritage and photography',
  '2 days in Kerala, budget 3000 per day, nature and relaxation',
];

// ─── Navbar scroll effect ────────────────────────────────────
window.addEventListener('scroll', () => {
  document.getElementById('navbar').classList.toggle('scrolled', window.scrollY > 40);
}, { passive: true });

// ─── Hamburger ───────────────────────────────────────────────
document.getElementById('hamburger').addEventListener('click', () => {
  document.getElementById('mobile-menu').classList.toggle('open');
});
function closeMobileMenu() {
  document.getElementById('mobile-menu').classList.remove('open');
}

// ─── Smooth scroll helpers ───────────────────────────────────
function scrollToPlanner() {
  document.getElementById('planner').scrollIntoView({ behavior: 'smooth' });
}

// ─── Example briefs ─────────────────────────────────────────
function setExample(idx) {
  document.getElementById('brief').value = EXAMPLE_BRIEFS[idx];
  document.getElementById('brief').focus();
  scrollToPlanner();
}

// ─── Utilities ───────────────────────────────────────────────
function fmtMin(minutes) {
  const h = Math.floor(minutes / 60).toString().padStart(2, '0');
  const m = (minutes % 60).toString().padStart(2, '0');
  return `${h}:${m}`;
}

function showToast(msg, type = '') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast${type ? ' ' + type : ''}`;
  el.classList.remove('hidden');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), 3500);
}

function apiHeaders() {
  const h = { 'Content-Type': 'application/json' };
  if (AUTH_TOKEN) h['Authorization'] = `Bearer ${AUTH_TOKEN}`;
  return h;
}

// ─── Server health check ─────────────────────────────────────
async function checkHealth() {
  const dot = document.getElementById('serverStatus').querySelector('.status-dot');
  const txt = document.getElementById('statusText');
  try {
    const r = await fetch(`${API_BASE}/health`);
    if (r.ok) {
      const d = await r.json();
      dot.className = 'status-dot online';
      txt.textContent = `Server online · DB: ${d.database} · LLM: ${d.llm || 'off'} · Places: ${d.places}`;
    } else {
      throw new Error('non-2xx');
    }
  } catch {
    dot.className = 'status-dot offline';
    txt.textContent = 'Server offline — start with: python -m tripplanner.server';
  }
}
checkHealth();

// ─── Plan form submission ─────────────────────────────────────
async function handlePlan(e) {
  e.preventDefault();
  const brief = document.getElementById('brief').value.trim();
  const userId = document.getElementById('user_id').value.trim();
  const saveTrip = document.getElementById('save_trip').checked;
  const useLlm = document.getElementById('use_llm').checked;

  if (!brief) return;

  // Validate save toggle requires user ID
  if (saveTrip && !userId) {
    showToast('⚠️ Enter a user ID to save your trip.', 'error');
    document.getElementById('user_id').focus();
    return;
  }

  setLoading(true);

  const body = { brief, llm: useLlm };
  if (saveTrip && userId) { body.save = true; body.user_id = userId; }

  try {
    const r = await fetch(`${API_BASE}/plan`, {
      method: 'POST',
      headers: apiHeaders(),
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'Unknown error');
    renderItinerary(data, brief);
    if (data.trip_id) showToast(`✅ Trip saved! ID: ${data.trip_id}`, 'success');
  } catch (err) {
    showError(err.message);
  } finally {
    setLoading(false);
  }
}

function setLoading(on) {
  const btn = document.getElementById('submitBtn');
  const txt = document.getElementById('btnText');
  const spin = document.getElementById('btnSpinner');
  btn.disabled = on;
  txt.classList.toggle('hidden', on);
  spin.classList.toggle('hidden', !on);
}

function showError(msg) {
  const wrap = document.getElementById('resultsWrap');
  document.getElementById('resultsEmpty').classList.add('hidden');
  const content = document.getElementById('resultsContent');
  content.classList.remove('hidden');
  content.innerHTML = `
    <div style="padding:32px; text-align:center; color:#fb7185;">
      <div style="font-size:2.5rem; margin-bottom:12px;">⚠️</div>
      <div style="font-weight:600; margin-bottom:6px;">Could not generate itinerary</div>
      <div style="font-size:.85rem; opacity:.8;">${escHtml(msg)}</div>
      <div style="font-size:.78rem; color:#94a3b8; margin-top:12px;">
        Make sure the server is running: <code>python -m tripplanner.server</code>
      </div>
    </div>`;
}

// ─── Render itinerary ─────────────────────────────────────────
function renderItinerary(data, brief) {
  const plan = data.plan;
  const explanations = data.explanations || [];
  const meta = data.meta || {};

  document.getElementById('resultsEmpty').classList.add('hidden');
  const content = document.getElementById('resultsContent');
  content.classList.remove('hidden');

  // Summary stats
  const totalStops = plan.days.reduce((s, d) => s + (d.stops || []).length, 0);
  const totalKm = plan.total_km ?? (plan.days.reduce((s, d) => s + (d.km || d.distance_km || 0), 0)).toFixed(1);
  const totalCost = plan.days.reduce((s, d) => s + (d.cost || 0), 0);

  let html = `
    <div class="itin-header">
      <div class="itin-title">📍 ${plan.days.length}-Day ${capitalize(plan.brief?.destination || 'Trip')} Itinerary</div>
      <div class="itin-meta">
        <span>📅 ${plan.days.length} day${plan.days.length > 1 ? 's' : ''}</span>
        <span>📌 ${totalStops} stops</span>
        <span>🚗 ${totalKm} km</span>
        <span>💰 ₹${totalCost} total</span>
        <span>⚡ ${meta.explainer || ''}</span>
      </div>
    </div>`;

  plan.days.forEach((day, idx) => {
    const note = explanations[idx] || '';
    const stops = day.stops || [];
    html += `
      <div class="day-card" id="day-${idx}">
        <div class="day-header" onclick="toggleDay(${idx})">
          <h3>Day ${day.day}</h3>
          <div class="day-meta">${stops.length} stop${stops.length !== 1 ? 's' : ''} · ${Number(day.km ?? day.distance_km ?? 0).toFixed(1)} km · ₹${day.cost || 0}</div>
          <span class="day-toggle">▾</span>
        </div>
        <div class="day-body">`;

    stops.forEach(stop => {
      // API stop shape: {id, name, arrive, depart, cost, travel_km, local_pick}
      // There is NO nested 'poi' object — fields are flat on the stop itself.
      const name     = stop.name      ?? stop.poi?.name      ?? '(unknown)';
      const cost     = stop.cost      ?? stop.poi?.cost      ?? 0;
      const isLocal  = stop.local_pick ?? (stop.poi ? !stop.poi.mainstream : false);
      // tags are not in the API response — skip gracefully
      const arrive   = typeof stop.arrive === 'number' ? fmtMin(stop.arrive) : (stop.arrive ?? '');
      const depart   = typeof stop.depart === 'number' ? fmtMin(stop.depart) : (stop.depart ?? '');
      const localBadge = isLocal ? `<span class="local-badge">🌿 local pick</span>` : '';
      html += `
        <div class="stop-row">
          <div class="stop-time">${escHtml(arrive)} – ${escHtml(depart)}</div>
          <div class="stop-info">
            <div class="stop-name">${escHtml(name)} ${localBadge}</div>
          </div>
          <div class="stop-cost">₹${cost}</div>
        </div>`;
    });

    if (note) {
      html += `<div class="day-note">💬 ${escHtml(note)}</div>`;
    }
    html += `</div></div>`;
  });

  // Unscheduled
  if (plan.unscheduled && plan.unscheduled.length > 0) {
    html += `
      <div class="unscheduled-box">
        <strong>⚠️ Could not schedule (budget / time constraints):</strong>
        ${plan.unscheduled.map(escHtml).join(', ')}
      </div>`;
  }

  // Actions
  html += `
    <div class="itin-actions">
      <button class="btn btn-ghost btn-sm" onclick="printItinerary()">🖨️ Print</button>
      <button class="btn btn-ghost btn-sm" onclick="copyBrief()">📋 Copy brief</button>
    </div>`;

  content.innerHTML = html;
  content.scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Store for copy
  content._brief = brief;
}

function toggleDay(idx) {
  document.getElementById(`day-${idx}`).classList.toggle('collapsed');
}

function printItinerary() {
  window.print();
}

function copyBrief() {
  const brief = document.getElementById('resultsContent')._brief || '';
  navigator.clipboard.writeText(brief).then(() => showToast('📋 Brief copied!', 'success'));
}

// ─── My Trips ────────────────────────────────────────────────
async function loadMyTrips() {
  const userId = document.getElementById('lookupUser').value.trim();
  if (!userId) {
    showToast('⚠️ Enter a user ID.', 'error');
    return;
  }

  const empty = document.getElementById('tripsEmpty');
  const list = document.getElementById('tripsList');
  empty.innerHTML = `<div class="empty-icon">⏳</div><p>Loading trips…</p>`;
  empty.classList.remove('hidden');
  list.classList.add('hidden');

  try {
    const r = await fetch(`${API_BASE}/trips?user_id=${encodeURIComponent(userId)}`, {
      headers: apiHeaders(),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'Failed to load trips.');

    const trips = data.trips || [];
    if (trips.length === 0) {
      empty.innerHTML = `<div class="empty-icon">🗂️</div><p>No saved trips for <strong>${escHtml(userId)}</strong>.</p>`;
      return;
    }

    empty.classList.add('hidden');
    list.classList.remove('hidden');
    list.innerHTML = trips.map(t => `
      <div class="trip-item" onclick="openTrip(${t.id}, '${escHtml(userId)}')">
        <div class="trip-item-id">Trip #${t.id}</div>
        <div class="trip-item-brief">${escHtml(t.brief || 'Trip')}</div>
        <div class="trip-item-date">Saved ${formatDate(t.saved_at)}</div>
      </div>`).join('');
  } catch (err) {
    empty.innerHTML = `<div class="empty-icon">⚠️</div><p style="color:#fb7185">${escHtml(err.message)}</p>`;
  }
}

async function openTrip(id, userId) {
  try {
    const r = await fetch(`${API_BASE}/trips/${id}?user_id=${encodeURIComponent(userId)}`, {
      headers: apiHeaders(),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || 'Failed to load trip.');

    const plan = data.plan;
    const explanations = data.explanations || [];
    const brief = data.brief || '';

    // Render in modal
    const modal = document.getElementById('tripModal');
    const content = document.getElementById('modalContent');

    // Build simplified view
    const totalStops = plan.days.reduce((s, d) => s + (d.stops || []).length, 0);
    let html = `
      <h2 style="font-family:'Playfair Display',serif;font-size:1.4rem;color:#f8fafc;margin-bottom:8px;">Trip #${id}</h2>
      <p style="font-size:.82rem;color:#94a3b8;margin-bottom:20px;">${escHtml(brief)}</p>`;

    plan.days.forEach((day, idx) => {
      const note = explanations[idx] || '';
      const stops = day.stops || [];
      html += `<div style="margin-bottom:16px;">
        <div style="font-size:.8rem;font-weight:700;color:#c4b5fd;margin-bottom:8px;text-transform:uppercase;letter-spacing:.05em;">Day ${day.day}</div>`;
      stops.forEach(stop => {
        const name   = stop.name  ?? stop.poi?.name  ?? '(unknown)';
        const cost   = stop.cost  ?? stop.poi?.cost  ?? 0;
        const arrive = typeof stop.arrive === 'number' ? fmtMin(stop.arrive) : (stop.arrive ?? '');
        const depart = typeof stop.depart === 'number' ? fmtMin(stop.depart) : (stop.depart ?? '');
        html += `<div style="display:flex;gap:10px;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05);">
          <span style="font-size:.7rem;color:#94a3b8;min-width:90px;">${escHtml(arrive)}–${escHtml(depart)}</span>
          <span style="font-size:.85rem;color:#e2e8f0;font-weight:600;">${escHtml(name)}</span>
          <span style="font-size:.75rem;color:#0ea5e9;margin-left:auto;">₹${cost}</span>
        </div>`;
      });
      if (note) {
        html += `<div style="margin-top:8px;font-size:.78rem;color:#94a3b8;font-style:italic;">${escHtml(note)}</div>`;
      }
      html += `</div>`;
    });

    content.innerHTML = html;
    modal.classList.remove('hidden');
  } catch (err) {
    showToast(`⚠️ ${err.message}`, 'error');
  }
}

function closeModal() {
  document.getElementById('tripModal').classList.add('hidden');
}
document.getElementById('tripModal').addEventListener('click', function (e) {
  if (e.target === this) closeModal();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// ─── Helpers ─────────────────────────────────────────────────
function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

function formatDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch { return iso; }
}

// ─── Press Enter in lookup field ─────────────────────────────
document.getElementById('lookupUser').addEventListener('keydown', e => {
  if (e.key === 'Enter') loadMyTrips();
});
