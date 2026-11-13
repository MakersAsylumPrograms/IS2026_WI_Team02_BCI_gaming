import {ApiError, typeInfo} from '../lib/api.js';
import {DemoAPI} from '../lib/demoApi.js';   // DEMO: swap for EqualCareAPI when integrating
import {createStore, startPolling} from '../lib/store.js';
import {createConnection, createSession} from '../lib/session.js';

// Nurse dashboard logic. No React in here: StaffApp.jsx draws whatever
// view() returns and re-draws whenever store.emit() runs.

// ---- Settings (safe to tweak) ---------------------------------------
const OVERDUE_AFTER_MS = 2 * 60 * 1000;  // PENDING longer than this = extra emphasis
const NEW_GLOW_MS = 8000;                // how long a new card glows
const ALERT_BANNER_MS = 10000;           // how long the "New request" banner stays
const POLL_MS = 1000;
const DEFAULT_PATIENT_NAME = 'Bed 1';    // if the backend does not send a name

// ---- Time helpers ----------------------------------------------------
const toMs = iso => Date.parse(iso);
export const clock = iso => new Date(iso).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});

// 75000 -> "1 min 15 s"
export function duration(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

export function createStaffApp() {
  const api = new DemoAPI('staff');
  const connection = createConnection();
  const store = createStore();

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
    alert: null,             // {id, text} of the "New request" banner, or null
    soundOn: false,
  };
  let stopPolling = null;
  let alertTimer = null;
  let alertCount = 0;

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

  // ---- Polling ---------------------------------------------------------
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
    alertCount += 1;   // new id = the banner animation restarts
    state.alert = {
      id: alertCount,
      text: `New request: ${fresh.map(r => typeInfo(r.type).label).join(', ')} — ${state.patientName}`,
    };
    clearTimeout(alertTimer);
    alertTimer = setTimeout(dismissAlert, ALERT_BANNER_MS);
    beep();
  }

  function dismissAlert() {
    clearTimeout(alertTimer);
    state.alert = null;
    store.emit();
  }

  // ---- Seen / Done -----------------------------------------------------
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

  // Keyboard shortcuts. Ignored while typing in the sign-in form.
  document.addEventListener('keydown', event => {
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
    if (!session.state.signedIn) return;
    const key = event.key.toLowerCase();
    const wanted = key === 's' ? 'PENDING' : key === 'd' ? 'ACKNOWLEDGED' : null;
    if (!wanted) return;
    const target = activeRequests().find(r => r.status === wanted);   // the top one
    if (target) { event.preventDefault(); act(target.id); }
  });

  // ---- Sound -----------------------------------------------------------
  let audio = null;

  function beep() {
    if (!audio) return;   // sound not enabled yet
    const start = audio.currentTime;
    // Two short, soft tones: high, then lower.
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

  // Browsers only allow sound after a click on the page.
  function enableSound() {
    try {
      audio ??= new AudioContext();
      audio.resume();
    } catch {
      state.notice = 'This browser cannot play alert sounds. Watch for the alert banner.';
      return render();
    }
    beep();   // test beep, so the nurse knows the volume is up
    state.soundOn = true;
    render();
  }

  // ---- What the view draws --------------------------------------------
  function describe(row, now) {
    const info = typeInfo(row.type);
    const created = toMs(row.created_at);
    const overdue = row.status === 'PENDING' && now - created > OVERDUE_AFTER_MS;
    let wait;
    if (row.status === 'PENDING') {
      wait = overdue ? `Waiting ${duration(now - created)} — overdue` : `Waiting ${duration(now - created)}`;
    } else if (row.status === 'ACKNOWLEDGED') {
      wait = `Seen after ${duration(toMs(row.acknowledged_at) - created)} · open ${duration(now - created)}`;
    } else {
      wait = `Done at ${clock(row.resolved_at)} · took ${duration(toMs(row.resolved_at) - created)}`;
    }
    const busy = state.busy.has(row.id);
    return {
      id: row.id,
      type: row.type,
      label: info.label,
      status: row.status,
      overdue,
      glowing: (state.glowUntil.get(row.id) ?? 0) > Date.now(),
      requestedAt: clock(row.created_at),
      wait,
      busy,
      action: row.status === 'RESOLVED' ? null : busy ? 'Saving…' : row.status === 'PENDING' ? 'Seen' : 'Done',
      actionLabel: `Mark ${info.label} as ${row.status === 'PENDING' ? 'seen' : 'done'}`,
    };
  }

  function view() {
    const now = api.serverNow();   // backend clock, so waiting times are correct
    const active = activeRequests();
    const resolved = resolvedRequests();
    const waiting = active.filter(r => r.status === 'PENDING').length;
    const seenTimes = state.requests
      .filter(r => r.acknowledged_at)
      .map(r => toMs(r.acknowledged_at) - toMs(r.created_at));
    const avgSeenMs = seenTimes.length ? seenTimes.reduce((a, b) => a + b, 0) / seenTimes.length : null;
    return {
      loaded: state.loaded,
      patientName: state.patientName || DEFAULT_PATIENT_NAME,
      active: active.map(r => describe(r, now)),
      resolved: resolved.map(r => describe(r, now)),
      waiting,
      inProgress: active.length - waiting,
      overdue: active.filter(r => r.status === 'PENDING' && now - toMs(r.created_at) > OVERDUE_AFTER_MS).length,
      avgSeen: avgSeenMs === null ? null : duration(avgSeenMs),
      summary: active.length ? `${waiting} waiting · ${active.length - waiting} in progress` : 'All clear',
      deviceConnected: state.deviceConnected,
      notice: state.pollError || state.notice,
      alert: state.alert,
      soundOn: state.soundOn,
    };
  }

  function render() {
    if (state.loaded) {
      const waiting = state.requests.filter(r => r.status === 'PENDING').length;
      document.title = waiting ? `(${waiting}) 🔴 EqualCare · Staff` : 'EqualCare · Staff';
    }
    store.emit();
  }

  // ---- Start / stop with sign-in ---------------------------------------
  const session = createSession(api, 'staff', {
    connection,
    onSignIn() {
      state.loaded = false;
      stopPolling = startPolling(refresh, POLL_MS);
    },
    onSignOut() {
      stopPolling?.();
      dismissAlert();
    },
  });

  return {
    store,
    session,
    connection,
    view,
    act,
    enableSound,
    dismissAlert,
  };
}
