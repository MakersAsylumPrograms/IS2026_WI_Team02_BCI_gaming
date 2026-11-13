import {ApiError, newId} from './api.js';

// DEMO ONLY: a pretend backend that lives in this browser.
//
// It has the same methods as EqualCareAPI (api.js) but never touches the
// network: requests are kept in localStorage, so a patient tab and a staff
// tab open side by side in the same browser see each other's changes.
// When the real backend is ready, swap `new DemoAPI(...)` back to
// `new EqualCareAPI(...)` in src/patient/controller.js and src/staff/controller.js.

const DB_KEY = 'equalcare.demo.requests.v1';
const SESSION_KEY = role => `equalcare.demo.session.${role}`;
const TYPES = ['WATER', 'TOILET_ASSISTANCE', 'REPOSITIONING', 'CALL_NURSE'];
const PATIENT = {name: 'Bed 1'};

let memory = {requests: [], keys: {}};   // used if localStorage is blocked

function load() {
  try { return JSON.parse(localStorage.getItem(DB_KEY)) || {requests: [], keys: {}}; } catch { return memory; }
}
function save(db) {
  memory = db;
  try { localStorage.setItem(DB_KEY, JSON.stringify(db)); } catch { /* memory only */ }
}
const wait = (ms = 120 + Math.random() * 150) => new Promise(resolve => setTimeout(resolve, ms));
const iso = () => new Date().toISOString();

export function resetDemo() {
  save({requests: [], keys: {}});
}

export class DemoAPI {
  constructor(role) {
    this.role = role;
    this.csrf = 'demo';
    this.clockOffsetMs = 0;
  }

  serverNow() { return Date.now(); }

  signedIn() {
    try { return localStorage.getItem(SESSION_KEY(this.role)) === '1'; } catch { return this._signedIn; }
  }
  requireSession() {
    if (!this.signedIn()) throw new ApiError('Not signed in.', 401, 'UNAUTHORIZED');
  }

  // ---- Session: any password works in the demo ----
  async login(role, password, loginId) {
    await wait();
    if (!password || (role === 'staff' && !loginId)) throw new ApiError('Invalid credentials.', 401, 'UNAUTHORIZED');
    this._signedIn = true;
    try { localStorage.setItem(SESSION_KEY(role), '1'); } catch { /* memory only */ }
    return {role, csrf: 'demo', patient: PATIENT};
  }
  async session() {
    await wait(80);
    this.requireSession();
    return {role: this.role, csrf: 'demo', patient: PATIENT};
  }
  async logout() {
    await wait();
    this._signedIn = false;
    try { localStorage.removeItem(SESSION_KEY(this.role)); } catch { /* memory only */ }
    return {};
  }

  // ---- Requests (same rules as the real backend) ----
  async requests() {
    await wait(60);
    this.requireSession();
    const {requests} = load();
    return {requests: [...requests].sort((a, b) => a.created_at.localeCompare(b.created_at)), patient: PATIENT};
  }

  async submit(type, key) {
    await wait();
    this.requireSession();
    if (!TYPES.includes(type) || !key) throw new ApiError('Valid type and idempotency_key are required.', 400, 'INVALID_REQUEST');
    const db = load();
    // Same key again = same request (safe retry). Same active type = same request.
    let request = db.requests.find(r => r.id === db.keys[key])
      ?? db.requests.find(r => r.type === type && r.status !== 'RESOLVED');
    if (!request) {
      const now = iso();
      request = {id: crypto.randomUUID?.() ?? newId(), type, status: 'PENDING', created_at: now, updated_at: now,
                 acknowledged_at: null, resolved_at: null};
      db.requests.push(request);
    }
    db.keys[key] = request.id;
    save(db);
    return {request};
  }

  async transition(id, action) {
    await wait();
    this.requireSession();
    const db = load();
    const request = db.requests.find(r => r.id === id);
    if (!request) throw new ApiError('Request not found.', 404, 'NOT_FOUND');
    const now = iso();
    if (action === 'acknowledge' && request.status === 'PENDING') {
      Object.assign(request, {status: 'ACKNOWLEDGED', acknowledged_at: now, updated_at: now});
    } else if (action === 'resolve' && request.status === 'ACKNOWLEDGED') {
      Object.assign(request, {status: 'RESOLVED', resolved_at: now, updated_at: now});
    }
    save(db);
    return {request};
  }

  // ---- ESP32: never connected in the demo (keyboard and mouse still work) ----
  async device() {
    await wait(40);
    return {device_id: 'esp32-demo', connected: false, seconds_since_seen: null};
  }
  async inputs() {
    await wait(250);
    this.requireSession();
    return {events: [], cursor: 0, epoch: 'demo'};
  }
}
