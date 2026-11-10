// Framework-independent client. Serve your frontend from this backend's origin.
export class ApiError extends Error {
  constructor(message, status, code) { super(message); this.status = status; this.code = code; }
}
export class EqualCareAPI {
  constructor() { this.csrf = ''; }
  async call(path, {method = 'GET', body, headers = {}} = {}) {
    const response = await fetch(`/api/v1${path}`, {
      method, credentials: 'same-origin', cache: 'no-store',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': this.csrf, ...headers},
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(1800),
    });
    const value = await response.json();
    if (!response.ok) throw new ApiError(value.error?.message || 'Request failed', response.status, value.error?.code);
    return value;
  }
  async login(role, password) {
    const result = await this.call('/session', {method: 'POST', body: {role, password}, headers: {'X-EqualCare-Client': 'dashboard'}});
    this.csrf = result.csrf; return result;
  }
  async session() { const result = await this.call('/session'); this.csrf = result.csrf; return result; }
  logout() { return this.call('/session', {method: 'DELETE'}); }
  requests() { return this.call('/requests'); }
  submit(type, idempotency_key) { return this.call('/requests', {method: 'POST', body: {type, idempotency_key}}); }
  transition(id, action) { return this.call(`/requests/${encodeURIComponent(id)}/${action}`, {method: 'POST'}); }
  device() { return this.call('/device'); }
  inputs(consumer, cursor, epoch) {
    const query = cursor === null ? '' : `?after=${cursor}&epoch=${encodeURIComponent(epoch)}`;
    return this.call(`/input-events${query}`, {headers: {'X-Consumer-ID': consumer}});
  }
}
// crypto.randomUUID requires HTTPS on LAN hosts; getRandomValues works on HTTP.
export function newId() {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
}
