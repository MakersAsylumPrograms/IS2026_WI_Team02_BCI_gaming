import {EqualCareAPI, ApiError, typeInfo} from './api.js';
import {$, setText, startPolling, createConnectionStatus, createSession} from './common.js';

// ---- Settings (safe to tweak) ---------------------------------------
// The backend is single-patient and has no bed/patient field, so every
// request belongs to this one label.
const OVERDUE_AFTER_MS = 2 * 60 * 1000;  // PENDING longer than this = extra emphasis
const NEW_GLOW_MS = 8000;                // how long a new card glows yellow
const ALERT_BANNER_MS = 10000;           // how long the "New request" banner stays
const POLL_MS = 1000;
const DEFAULT_PATIENT_NAME = 'Bed 1';    // if the backend does not send a name

const BADGE = {PENDING: 'WAITING', ACKNOWLEDGED: 'SEEN', RESOLVED: 'DONE'};

const api = new EqualCareAPI('staff');
const connection = createConnectionStatus();

// ---- State -----------------------------------------------------------
const state = {
  patientName: '',
  requests: [],            // latest list from the backend
  loaded: false,           // got the list at least once since sign-in?
  knownIds: new Set(),     // requests we've already alerted about
  glowUntil: new Map(),    // request id -> time its "new" glow ends
  busy: new Set(),         // request ids with a Seen/Done call in flight
  deviceConnected: null,   // ESP32 status (null = unknown)
  notice: '',              // error message to show, if any
  pollError: '',           // last refresh failed for a reason other than the network
};
const cards = new Map();   // request id -> its card element (re-used, see cardFor)
let stopPolling = null;
let alertTimer = null;

// ---- Time helpers ----------------------------------------------------
const toMs = iso => Date.parse(iso);
const clock = iso => new Date(iso).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});

// 75000 -> "1 min 15 s"
function duration(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

// ---- Ordering --------------------------------------------------------
function activeRequests() {
  const rank = r => (r.status === 'PENDING' ? 0 : 1);
  return state.requests
    .filter(r => r.status !== 'RESOLVED')
    .sort((a, b) => rank(a) - rank(b) || toMs(a.created_at) - toMs(b.created_at));
}
function resolvedRequests() {
  return state.requests
    .filter(r => r.status === 'RESOLVED')
    .sort((a, b) => toMs(b.resolved_at) - toMs(a.resolved_at));
}

// Polling
async function refresh() {
  try {
    const {requests, patient} = await api.requests();
    state.patientName = patient?.name || DEFAULT_PATIENT_NAME;
    alertForNew(requests);
    // If we just pressed Seen/Done, our copy may be newer than a poll that
    // started a moment earlier. Keep whichever was updated most recently.
    const mine = new Map(state.requests.map(r => [r.id, r]));
    state.requests = requests.map(r => {
      const local = mine.get(r.id);
      return local && local.updated_at > r.updated_at ? local : r;
    });
    state.loaded = true;
    state.pollError = '';
    connection.ok();
  } catch (error) {
    if (error.status === 401) return session.expire();
    if (error instanceof ApiError) {
      connection.failed();
    } else {
      // The server answered but this page could not use the reply (e.g. the
      // backend is an older version than these files). Say so; it is not a
      // network problem, so "Reconnecting…" would be misleading.
      console.error(error);
      connection.ok();
      state.pollError = `Cannot show requests: the server's reply was not understood (${error.message}). ` +
                        'Restart the backend so it matches this page, then reload.';
    }
  }
  try { state.deviceConnected = (await api.device()).connected; } catch { state.deviceConnected = null; }
  render();   // also runs while offline, so the waiting timers keep ticking
}

// Find PENDING requests we have never seen before and alert about them.
function alertForNew(rows) {
  const fresh = rows.filter(r => r.status === 'PENDING' && !state.knownIds.has(r.id));
  rows.forEach(r => state.knownIds.add(r.id));
  // First load after opening the page: those requests are shown (in red)
  // but we don't sound an alarm for things that were already there.
  if (!state.loaded || fresh.length === 0) return;

  fresh.forEach(r => state.glowUntil.set(r.id, Date.now() + NEW_GLOW_MS));
  const banner = $('alert-banner');
  banner.textContent = `🔔 New request: ${fresh.map(r => typeInfo(r.type).label).join(', ')} — ${state.patientName}`;
  banner.hidden = false;
  banner.classList.remove('alert');      // restart the flash animation
  void banner.offsetWidth;
  banner.classList.add('alert');
  clearTimeout(alertTimer);
  alertTimer = setTimeout(() => { banner.hidden = true; }, ALERT_BANNER_MS);
  beep();
}

// Seen / Done
async function act(id) {
  const row = state.requests.find(r => r.id === id);
  if (!row || row.status === 'RESOLVED' || state.busy.has(id)) return;   // nothing to do / already working
  const action = row.status === 'PENDING' ? 'acknowledge' : 'resolve';

  state.busy.add(id);            // disables the button until the call returns
  render();
  try {
    const {request} = await api.transition(id, action);
    state.requests = state.requests.map(r => (r.id === id ? request : r));
    state.notice = '';
  } catch (error) {
    if (error.status === 401) return session.expire();
    // Seen/Done are safe to repeat, so just say so.
    state.notice = `Could not update ${typeInfo(row.type).label}: ${error.message} It is safe to try again.`;
  } finally {
    state.busy.delete(id);
    render();
  }
}

// Keyboard shortcuts (bonus). Ignored while typing in the password box.
document.addEventListener('keydown', event => {
  if (event.target.tagName === 'INPUT' || event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
  if ($('app').hidden) return;
  const key = event.key.toLowerCase();
  const wanted = key === 's' ? 'PENDING' : key === 'd' ? 'ACKNOWLEDGED' : null;
  if (!wanted) return;
  const target = activeRequests().find(r => r.status === wanted);   // the top one
  if (target) { event.preventDefault(); act(target.id); }
});

let audio = null;

function beep() {
  if (!audio) return;   // sound not enabled yet
  const start = audio.currentTime;
  // Two short tones: high, then lower.
  [[880, 0], [660, 0.25]].forEach(([frequency, delay]) => {
    const osc = audio.createOscillator();
    const gain = audio.createGain();
    osc.frequency.value = frequency;
    gain.gain.setValueAtTime(0.0001, start + delay);
    gain.gain.exponentialRampToValueAtTime(0.4, start + delay + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + delay + 0.2);
    osc.connect(gain).connect(audio.destination);
    osc.start(start + delay);
    osc.stop(start + delay + 0.22);
  });
}

$('sound').addEventListener('click', () => {
  try {
    audio ??= new AudioContext();
    audio.resume();
  } catch {
    state.notice = 'This browser cannot play alert sounds. Watch for the yellow banner.';
    return render();
  }
  beep();   // test beep, so the nurse knows the volume is up
  $('sound').textContent = '🔔 Sound alerts on — click to test';
  $('sound').classList.add('on');
});

function cardFor(row, now) {
  let card = cards.get(row.id);
  if (!card) {
    card = document.createElement('article');
    const icon = document.createElement('span');
    icon.className = 'icon';
    icon.setAttribute('aria-hidden', 'true');
    const body = document.createElement('div');
    const heading = document.createElement('div');
    const type = document.createElement('span');
    type.className = 'type';
    const badge = document.createElement('span');
    badge.className = 'badge';
    heading.append(type, badge);
    const meta = document.createElement('div');
    meta.className = 'meta';
    const wait = document.createElement('div');
    wait.className = 'wait';
    body.append(heading, meta, wait);
    const button = document.createElement('button');
    button.className = 'action';
    button.addEventListener('click', () => act(row.id));
    card.append(icon, body, button);
    card.parts = {icon, type, badge, meta, wait, button};
    cards.set(row.id, card);
  }

  const {icon, type, badge, meta, wait, button} = card.parts;
  const info = typeInfo(row.type);
  const created = toMs(row.created_at);
  const overdue = row.status === 'PENDING' && now - created > OVERDUE_AFTER_MS;
  const glowing = (state.glowUntil.get(row.id) ?? 0) > Date.now();

  card.className = ['request', row.status.toLowerCase(), overdue && 'overdue', glowing && 'new']
    .filter(Boolean).join(' ');
  setText(icon, info.icon);
  setText(type, info.label);
  setText(badge, BADGE[row.status]);
  setText(meta, `${state.patientName} · ID ${row.id.slice(0, 8)} · requested ${clock(row.created_at)}`);
  meta.title = `Full request ID: ${row.id}`;

  if (row.status === 'PENDING') {
    setText(wait, overdue ? `⚠ Waiting ${duration(now - created)} — overdue` : `Waiting ${duration(now - created)}`);
  } else if (row.status === 'ACKNOWLEDGED') {
    setText(wait, `Seen after ${duration(toMs(row.acknowledged_at) - created)} · open ${duration(now - created)}`);
  } else {
    setText(wait, `Done at ${clock(row.resolved_at)} · took ${duration(toMs(row.resolved_at) - created)}`);
  }

  // Seen on PENDING, Done on ACKNOWLEDGED, no button once RESOLVED.
  const busy = state.busy.has(row.id);
  button.hidden = row.status === 'RESOLVED';
  setText(button, busy ? 'Saving…' : row.status === 'PENDING' ? 'Seen' : 'Done');
  button.disabled = busy;
  button.setAttribute('aria-label',
    `Mark ${info.label} as ${row.status === 'PENDING' ? 'seen' : 'done'}`);
  return card;
}

// Put the cards in `container` in the given order (only touches the DOM
// if the order actually changed).
function placeCards(container, rows, now) {
  const wanted = rows.map(row => cardFor(row, now));
  const current = [...container.children];
  const same = wanted.length === current.length && wanted.every((card, i) => card === current[i]);
  if (!same) container.replaceChildren(...wanted);
}

function render() {
  setText($('notice'), state.pollError || state.notice);   // shown even before the first load
  if (!state.loaded) return;
  const now = api.serverNow();   // backend clock, so waiting times are correct
  const active = activeRequests();
  const resolved = resolvedRequests();

  placeCards($('active'), active, now);
  placeCards($('history'), resolved, now);
  $('active-empty').hidden = active.length > 0;
  $('history-empty').hidden = resolved.length > 0;
  setText($('history-summary'), `Resolved history (${resolved.length})`);

  const waiting = active.filter(r => r.status === 'PENDING').length;
  const seen = active.length - waiting;
  setText($('summary'), active.length ? `${waiting} waiting · ${seen} in progress` : 'All clear');
  document.title = waiting ? `(${waiting}) 🔴 EqualCare · Staff` : 'EqualCare · Staff';

  setText($('device'), state.deviceConnected === null ? ''
    : state.deviceConnected ? `${state.patientName} ESP32 controller: connected`
    : `${state.patientName} ESP32 controller: not connected (patient can still use keyboard/mouse)`);
}

// Start / stop with sign-in
const session = createSession(api, 'staff', {
  onSignIn() {
    state.loaded = false;
    stopPolling = startPolling(refresh, POLL_MS);
  },
  onSignOut() {
    stopPolling?.();
  },
});
