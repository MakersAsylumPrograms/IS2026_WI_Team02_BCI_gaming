import {EqualCareAPI, newId} from './api.js';
const api = new EqualCareAPI();
const role = document.body.dataset.role;
const $ = id => document.getElementById(id);
const labels = {WATER: 'Water', TOILET_ASSISTANCE: 'Toilet assistance', REPOSITIONING: 'Repositioning', CALL_NURSE: 'Call nurse'};
const types = Object.keys(labels);
const statusLabels = {PENDING: 'Waiting for nurse', ACKNOWLEDGED: 'Acknowledged by nurse', RESOLVED: 'Resolved'};
let active = false, selected = 0, sending = false, cursor = null, epoch = '', generation = 0;
let consumer = newId(), inputReady = false, lastSnapshot = '';
const pendingKey = 'equalcare.pending.v1';
let pending = null;
try { pending = JSON.parse(localStorage.getItem(pendingKey)); } catch { /* no saved submission */ }
const message = text => { $('message').textContent = text; };

function select(index) {
  selected = (index + types.length) % types.length;
  document.querySelectorAll('#menu button').forEach((button, i) => {
    button.classList.toggle('selected', i === selected);
    button.setAttribute('aria-pressed', String(i === selected));
  });
}
async function submit(type, retry = false) {
  if (sending) return;
  if (pending && !retry) { message('An earlier request is unconfirmed. Use Retry before sending another.'); return; }
  if (!pending) {
    pending = {type, idempotency_key: newId()};
    try { localStorage.setItem(pendingKey, JSON.stringify(pending)); }
    catch { pending = null; message('Browser storage is unavailable. Enable it before sending requests.'); return; }
  }
  sending = true;
  message('Sending request…');
  try {
    const result = await api.submit(pending.type, pending.idempotency_key);
    localStorage.removeItem(pendingKey); pending = null;
    message(`${labels[result.request.type]}: ${statusLabels[result.request.status]}.`);
  } catch (error) {
    message(`Delivery uncertain. Retry uses the same request ID. ${error.message}`);
  } finally { sending = false; $('retry').hidden = !pending; }
}
function command(value) {
  if (!active || !inputReady || sending || pending) return;
  if (value === 'NEXT') select(selected + 1);
  if (value === 'PREVIOUS') select(selected - 1);
  if (value === 'SELECT') void submit(types[selected]);
}

function renderRequests(rows) {
  const snapshot = JSON.stringify(rows);
  if (snapshot === lastSnapshot) return;
  lastSnapshot = snapshot;
  $('active').replaceChildren(); $('history').replaceChildren();
  for (const row of rows) {
    const card = document.createElement('article'); card.className = 'request';
    const info = document.createElement('div');
    const title = document.createElement('strong'); title.textContent = labels[row.type] || row.type;
    const status = document.createElement('span'); status.className = 'badge'; status.textContent = statusLabels[row.status];
    const detail = document.createElement('small'); detail.textContent = `${new Date(row.created_at).toLocaleString()} · ${row.id}`;
    info.append(title, status, detail); card.append(info);
    if (role === 'staff' && row.status !== 'RESOLVED') {
      const button = document.createElement('button');
      const action = row.status === 'PENDING' ? 'acknowledge' : 'resolve';
      button.textContent = action === 'acknowledge' ? 'Acknowledge' : 'Resolve';
      button.addEventListener('click', async () => {
        button.disabled = true;
        try { await api.transition(row.id, action); lastSnapshot = ''; message('Request updated.'); }
        catch (error) { message(`Update not confirmed; it is safe to retry. ${error.message}`); }
        finally { button.disabled = false; }
      });
      card.append(button);
    }
    $(row.status === 'RESOLVED' ? 'history' : 'active').append(card);
  }
  if (!$('active').children.length) $('active').textContent = 'No active requests.';
  if (!$('history').children.length) $('history').textContent = 'No completed requests yet.';
}
async function pollStatus(token) {
  if (!active || token !== generation) return;
  try {
    const [requests, device] = await Promise.all([api.requests(), api.device()]);
    if (!active || token !== generation) return;
    renderRequests(requests.requests);
    $('connection').textContent = 'Backend connected';
    $('device').textContent = device.connected ? 'ESP32 connected'
      : role === 'patient' ? 'ESP32 disconnected · touch controls available' : 'Patient ESP32 disconnected';
  } catch (error) {
    $('connection').textContent = 'Backend unavailable · displayed statuses may be stale';
    $('device').textContent = 'ESP32 status unknown';
    if (error.status === 401) { stop(); message('Session expired. Sign in again.'); return; }
  }
  setTimeout(() => pollStatus(token), 1000);
}
async function pollInputs(token) {
  if (!active || token !== generation) return;
  if (document.hidden) {
    cursor = null; inputReady = false;
    setTimeout(() => pollInputs(token), 250); return;
  }
  try {
    const result = await api.inputs(consumer, cursor, epoch);
    if (!active || token !== generation) return;
    if (document.hidden) {
      cursor = null; inputReady = false;
      setTimeout(() => pollInputs(token), 250); return;
    }
    cursor = result.cursor; epoch = result.epoch; inputReady = true;
    $('input-status').textContent = 'Device and keyboard controls ready';
    for (const event of result.events) command(event.command);
  } catch (error) {
    cursor = null; inputReady = false;
    $('input-status').textContent = error.code === 'CONSUMER_BUSY'
      ? 'Another patient tab is using device controls. Close it and wait three seconds.'
      : 'Device controls paused; reconnecting without replaying old commands.';
  }
  setTimeout(() => pollInputs(token), 250);
}
function start() {
  active = true; generation++; cursor = null; inputReady = false; consumer = newId(); lastSnapshot = '';
  $('login').hidden = true; $('dashboard').hidden = false; $('logout').hidden = false;
  message(pending && role === 'patient' ? 'An earlier submission is unconfirmed. Retry to check its result.' : 'Connected.');
  if (role === 'patient') { $('retry').hidden = !pending; void pollInputs(generation); }
  void pollStatus(generation);
}
function stop() {
  active = false; inputReady = false; generation++;
  $('login').hidden = false; $('dashboard').hidden = true; $('logout').hidden = true;
}
$('login').addEventListener('submit', async event => {
  event.preventDefault();
  try { await api.login(role, $('password').value); $('password').value = ''; start(); }
  catch (error) { message(error.message); }
});
$('logout').addEventListener('click', async () => {
  try { await api.logout(); stop(); message('Signed out.'); }
  catch (error) { message(`Sign out failed: ${error.message}`); }
});
if (role === 'patient') {
  for (const [i, type] of types.entries()) {
    const button = document.createElement('button'); button.textContent = labels[type];
    button.addEventListener('click', () => { select(i); void submit(type); }); $('menu').append(button);
  }
  select(0);
  $('retry').addEventListener('click', () => void submit(null, true));
  document.addEventListener('keydown', event => {
    if (active && event.repeat && ['Enter', ' '].includes(event.key)) event.preventDefault();
    if (!active || event.repeat || ['INPUT', 'BUTTON', 'SUMMARY'].includes(document.activeElement.tagName)) return;
    const mapping = {ArrowRight: 'NEXT', ArrowDown: 'NEXT', ArrowLeft: 'PREVIOUS', ArrowUp: 'PREVIOUS', Enter: 'SELECT'};
    if (mapping[event.key]) { event.preventDefault(); command(mapping[event.key]); }
  });
  document.addEventListener('visibilitychange', () => { cursor = null; inputReady = false; });
}
try { const current = await api.session(); if (current.role === role) start(); }
catch { /* Show sign-in form for a new session. */ }
