// =====================================================================
// api.js — the ONLY file that talks to the backend over HTTP.
//
// Both screens (patient + staff) import this. If an endpoint or field
// name ever changes, this is the one place to update.
// Backend contract: Documentation/openapi.json
// =====================================================================

// ---------------------------------------------------------------------
// API_BASE — where the backend API lives. THE one setting for networking.
//
// Keep this a same-origin path ('/api/v1'). The backend serves these
// pages AND the API from the same address, so whatever address you type
// into the browser is automatically the backend address:
//   patient laptop (runs the backend):  http://localhost:8000/patient/
//   staff laptop (same hotspot):        http://<patient-laptop-IP>:8000/staff/
//
// Do NOT point this at another machine (e.g. 'http://192.168.1.5:8000/api/v1').
// The backend deliberately disables CORS and uses SameSite=Strict cookies,
// so a cross-origin API call would be blocked by the browser.
// ---------------------------------------------------------------------
export const API_BASE = '/api/v1';

// Give up on a call after this long, so a dead network shows "Reconnecting"
// quickly instead of hanging. Polls run every 1 s, so keep this under ~2 s.
const TIMEOUT_MS = 1800;

// The request types the backend accepts (app.py TYPES) and how we show them.
// Order here = order on the patient menu.
export const REQUEST_TYPES = [
  {type: 'WATER',             label: 'Water',             icon: '💧'},
  {type: 'TOILET_ASSISTANCE', label: 'Toilet assistance', icon: '🚻'},
  {type: 'REPOSITIONING',     label: 'Adjust position',   icon: '🛏️'},
  {type: 'CALL_NURSE',        label: 'Call nurse',        icon: '🔔'},
];

// Look up the display info for a backend type string, e.g. 'WATER'.
export function typeInfo(type) {
  return REQUEST_TYPES.find(t => t.type === type) || {type, label: type, icon: '❓'};
}

// Every failed call throws one of these.
//   status 0   -> we never got an answer (backend down, Wi-Fi gone, timeout)
//   status 401 -> not signed in / session expired
//   other      -> the backend answered with an error (see .code)
export class ApiError extends Error {
  constructor(message, status, code) {
    super(message);
    this.status = status;
    this.code = code;
  }
  get offline() { return this.status === 0; }
}

export class EqualCareAPI {
  constructor() {
    this.csrf = '';           // anti-forgery token; must be sent on every write
    this.clockOffsetMs = 0;   // serverTime - thisLaptopTime (see serverNow())
  }

  // Low-level helper used by every method below.
  async call(path, {method = 'GET', body, headers = {}} = {}) {
    let response;
    try {
      response = await fetch(API_BASE + path, {
        method,
        credentials: 'same-origin',   // send the session cookie
        cache: 'no-store',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': this.csrf, ...headers},
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch {
      // fetch() itself failed: no network, server stopped, or timeout.
      throw new ApiError('Cannot reach the EqualCare server.', 0, 'NETWORK');
    }

    // Track the server's clock so "time waiting" is right even if the two
    // laptops' clocks disagree. The Date header has 1-second resolution.
    const serverDate = Date.parse(response.headers.get('Date'));
    if (!Number.isNaN(serverDate)) this.clockOffsetMs = serverDate - Date.now();

    // Some failures (e.g. a proxy error page) are not JSON; don't crash on them.
    let value = {};
    try { value = await response.json(); } catch { /* non-JSON body */ }

    if (!response.ok) {
      throw new ApiError(value.error?.message || `Server error (${response.status}).`,
                         response.status, value.error?.code);
    }
    return value;
  }

  // Current time according to the backend's clock (milliseconds).
  serverNow() { return Date.now() + this.clockOffsetMs; }

  // ---- Session (sign in / out) ----
  async login(role, password) {
    const result = await this.call('/session', {
      method: 'POST', body: {role, password},
      headers: {'X-EqualCare-Client': 'dashboard'},   // backend requires this header
    });
    this.csrf = result.csrf;
    return result;
  }
  // After a page refresh: ask the backend if our cookie is still signed in.
  async session() {
    const result = await this.call('/session');
    this.csrf = result.csrf;
    return result;
  }
  logout() { return this.call('/session', {method: 'DELETE'}); }

  // ---- Requests ----
  // -> {requests: [{id, type, status, created_at, updated_at, acknowledged_at, resolved_at}]}
  requests() { return this.call('/requests'); }

  // Patient only. Re-sending with the SAME idempotency_key never creates a
  // duplicate, so it is always safe to retry an uncertain send with its key.
  submit(type, idempotency_key) {
    return this.call('/requests', {method: 'POST', body: {type, idempotency_key}});
  }

  // Staff only. action is 'acknowledge' (Seen) or 'resolve' (Done).
  transition(id, action) {
    return this.call(`/requests/${encodeURIComponent(id)}/${action}`, {method: 'POST'});
  }

  // ---- ESP32 ----
  // -> {device_id, connected, seconds_since_seen}
  device() { return this.call('/device'); }

  // Patient only: fetch NEXT/PREVIOUS/SELECT events the ESP32 sent to the backend.
  // See input.js (startDevicePolling) for how these are used.
  inputs(consumer, cursor, epoch) {
    const query = cursor === null ? '' : `?after=${cursor}&epoch=${encodeURIComponent(epoch)}`;
    return this.call(`/input-events${query}`, {headers: {'X-Consumer-ID': consumer}});
  }
}

// Random ID (32 hex chars). crypto.randomUUID() only works on HTTPS or
// localhost, but the staff laptop uses plain http://<IP>, so use this instead.
export function newId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),
                    b => b.toString(16).padStart(2, '0')).join('');
}
