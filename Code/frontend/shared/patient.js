// =====================================================================
// patient.js — the patient screen.
//
// The screen is a tiny "state machine". It is always on exactly one of
// these screens, and every screen is just a title + a list of options:
//
//   menu ──SELECT──> confirm ──Confirm──> sending ──ok──> status ──Done──> menu
//                       │                    │              (Sent → Help is coming ✓ → Done ✓)
//                     Cancel             no answer
//                       ▼                    ▼
//                     menu               uncertain ──Try again──> sending
//                                            └──────Cancel──────> menu
//
// Input arrives ONLY through handleCommand() (see input.js), so the
// keyboard, the mouse and the ESP32 all drive the screen the same way.
// Every screen can be completed with just NEXT + SELECT: "Cancel" and
// "Back to menu" are real options on screen, not only the Escape key.
// =====================================================================
import {EqualCareAPI, REQUEST_TYPES, typeInfo, newId} from './api.js';
import {createInput, startDevicePolling, NEXT, PREVIOUS, SELECT, BACK} from './input.js';
import {$, startPolling, createConnectionStatus, createSession} from './common.js';

// ---- Settings (safe to tweak) ---------------------------------------
// On the confirm screen both "Confirm" and "Cancel" are shown. This picks
// which one is highlighted first. Starting on Cancel means an accidental
// double-SELECT can never send an alert: sending needs NEXT, then SELECT.
const CONFIRM_STARTS_ON_CANCEL = true;
const RETURN_TO_MENU_AFTER_MS = 5000;    // after "Done ✓"
const POLL_MS = 1000;                    // how often we refresh request status
const PENDING_KEY = 'equalcare.pending.v1';

// What the patient sees for each backend status.
const STATUS_TEXT = {
  PENDING:      {title: 'Request sent ✓',   detail: 'Waiting for a nurse to see it…', short: 'Sent – waiting'},
  ACKNOWLEDGED: {title: 'Help is coming ✓', detail: 'A nurse has seen your request.', short: 'Help is coming ✓'},
  RESOLVED:     {title: 'Done ✓',           detail: 'Going back to the menu…',         short: 'Done'},
};
const STEP_FOR_STATUS = {PENDING: 0, ACKNOWLEDGED: 1, RESOLVED: 2};   // for the Sent/Seen/Done bar

const api = new EqualCareAPI();
const connection = createConnectionStatus();

// ---- State: everything the screen needs to draw itself -------------
const state = {
  signedIn: false,
  screen: 'menu',    // 'menu' | 'confirm' | 'sending' | 'status' | 'uncertain'
  highlight: 0,      // which option is highlighted on the current screen
  chosen: null,      // request type picked on the menu, e.g. 'WATER'
  trackedId: null,   // id of the request shown on the status screen
  requests: [],      // latest list from the backend
  loaded: false,     // true once we have the list at least once
  notice: '',        // one-off message shown on the menu (e.g. an error)
  device: 'starting',        // device polling status (see input.js)
  deviceConnected: false,    // is the ESP32 sending heartbeats?
};
let returnTimer = null;      // timer that goes back to the menu after "Done"
let stopRequestPolling = null, stopDevicePolling = null;

// ---------------------------------------------------------------------
// "Pending" = a send whose result we don't know yet (e.g. Wi-Fi dropped
// mid-request). We save its idempotency key in localStorage so that, even
// after a page refresh, "Try again" re-uses the SAME key and the backend
// can never create a duplicate.
// ---------------------------------------------------------------------
let pending = null;
try { pending = JSON.parse(localStorage.getItem(PENDING_KEY)); } catch { /* none saved */ }

function setPending(value) {
  pending = value;
  try {
    if (value) localStorage.setItem(PENDING_KEY, JSON.stringify(value));
    else localStorage.removeItem(PENDING_KEY);
  } catch { /* storage blocked: still works, just not across a refresh */ }
}

// ---- Small lookups ---------------------------------------------------
const trackedRequest = () => state.requests.find(r => r.id === state.trackedId);
const activeRequestOf = type => state.requests.find(r => r.type === type && r.status !== 'RESOLVED');
const newestActive = () => state.requests.filter(r => r.status !== 'RESOLVED').at(-1);  // list is oldest-first

// Add/replace one request in our local list (after we send one).
function upsert(request) {
  state.requests = state.requests.filter(r => r.id !== request.id).concat(request);
}

// Switch screen. `highlight` = which option starts highlighted.
function show(screen, highlight = 0) {
  clearTimeout(returnTimer);
  returnTimer = null;
  state.screen = screen;
  state.highlight = highlight;
  render();
}
// Back to the menu, keeping the highlight on the item the patient last picked.
function goToMenu() {
  show('menu', Math.max(0, REQUEST_TYPES.findIndex(t => t.type === state.chosen)));
}

// =====================================================================
// SCREENS — each returns {title, detail, options, back}
//   options: [{icon, label, note, run}]  (run() is called on SELECT)
//   back:    what BACK (Escape) does, or null
// =====================================================================
function currentScreen() {
  const chosen = typeInfo(state.chosen);

  if (state.screen === 'menu') {
    return {
      title: 'How can we help?',
      detail: state.notice || 'Move to what you need, then select it.',
      options: REQUEST_TYPES.map(t => {
        const active = activeRequestOf(t.type);
        return {icon: t.icon, label: t.label, note: active ? STATUS_TEXT[active.status].short : '',
                run: () => chooseType(t.type)};
      }),
      back: null,
    };
  }

  if (state.screen === 'confirm') {
    return {
      title: `${chosen.icon} ${chosen.label}?`,
      detail: 'Send this request to the nurse?',
      options: [
        {icon: '✓', label: 'Confirm', run: () => send(state.chosen)},
        {icon: '✕', label: 'Cancel',  run: goToMenu},
      ],
      back: goToMenu,
    };
  }

  if (state.screen === 'sending') {
    return {title: 'Sending…', detail: `Sending your ${chosen.label} request.`, options: [], back: null};
  }

  if (state.screen === 'uncertain') {
    return {
      title: 'Not sure it was sent',
      detail: `We could not confirm your ${chosen.label} request reached the nurse. ` +
              `Trying again is safe — it will not send a duplicate.`,
      options: [
        {icon: '↻', label: 'Try again', run: () => send(state.chosen)},
        {icon: '✕', label: 'Cancel', run: () => { setPending(null); goToMenu(); }},
      ],
      back: null,   // must choose one: we don't want to silently forget it
    };
  }

  // 'status'
  const request = trackedRequest();
  const info = typeInfo(request?.type ?? state.chosen);
  const text = STATUS_TEXT[request?.status] || STATUS_TEXT.PENDING;
  return {
    title: text.title,
    detail: `${info.icon} ${info.label} — ${text.detail}`,
    options: [{icon: '←', label: 'Back to menu', run: goToMenu}],
    back: goToMenu,
    step: STEP_FOR_STATUS[request?.status] ?? 0,
  };
}

// ---- Actions ---------------------------------------------------------

// Picked something on the menu.
function chooseType(type) {
  state.chosen = type;
  state.notice = '';
  const active = activeRequestOf(type);
  if (active) {
    // Already asked for this and it's not done: just show its status
    // instead of asking again.
    state.trackedId = active.id;
    show('status');
  } else {
    show('confirm', CONFIRM_STARTS_ON_CANCEL ? 1 : 0);
  }
}

// Send a request. Only shows "sent" after the backend confirms it.
async function send(type) {
  if (state.screen === 'sending') return;
  // Re-use the saved key when retrying the same type, otherwise make a new one.
  if (!pending || pending.type !== type) setPending({type, idempotency_key: newId()});
  state.chosen = type;
  show('sending');
  try {
    const {request} = await api.submit(pending.type, pending.idempotency_key);
    setPending(null);                 // confirmed: forget the key
    upsert(request);
    state.trackedId = request.id;
    show('status');
  } catch (error) {
    if (error.status === 401 || error.status === 403) {
      // Keep the pending key: after signing in again we'll offer "Try again".
      session.expire('Signed out before the request was confirmed. Please sign in again.');
    } else if (error.offline || error.status >= 500) {
      // No answer, or the server had a temporary problem: it MAY have been saved.
      show('uncertain');
    } else {
      // The backend clearly said no (4xx): nothing was saved.
      setPending(null);
      state.notice = `Not sent: ${error.message}`;
      goToMenu();
    }
  }
}

// =====================================================================
// handleCommand — THE single entry point for all input.
// =====================================================================
function handleCommand(command, detail) {
  if (!state.signedIn || !state.loaded) return;
  const screen = currentScreen();
  const count = screen.options.length;

  // Mouse click: move the highlight to the clicked option first.
  if (detail.index !== undefined && detail.index < count) state.highlight = detail.index;

  if (command === NEXT && count) state.highlight = (state.highlight + 1) % count;
  if (command === PREVIOUS && count) state.highlight = (state.highlight - 1 + count) % count;
  if (command === BACK && screen.back) return screen.back();
  if (command === SELECT && count) return screen.options[state.highlight].run();
  render();
}
const input = createInput(handleCommand);

// =====================================================================
// Polling the backend for request status (every POLL_MS)
// =====================================================================
async function refresh() {
  try {
    const {requests} = await api.requests();
    // A poll that started just BEFORE our send finished won't include the
    // new request yet. Keep our copy so the status screen doesn't flicker.
    const local = trackedRequest();
    state.requests = requests;
    if (local && !requests.some(r => r.id === local.id)) state.requests.push(local);
    connection.ok();
    if (!state.loaded) { state.loaded = true; recoverAfterLoad(); }
  } catch (error) {
    if (error.status === 401) return session.expire();
    connection.failed();
  }
  try { state.deviceConnected = (await api.device()).connected; } catch { state.deviceConnected = false; }

  // On the status screen: once it's Done, go back to the menu shortly.
  if (state.screen === 'status') {
    const request = trackedRequest();
    if (request?.status === 'RESOLVED' && !returnTimer) {
      returnTimer = setTimeout(goToMenu, RETURN_TO_MENU_AFTER_MS);
    }
  }
  render();
}

// First load after sign-in or a page refresh: pick up where we left off,
// using what the BACKEND says (never guessing).
function recoverAfterLoad() {
  if (pending) {
    state.chosen = pending.type;
    const match = activeRequestOf(pending.type);
    if (match) {                       // it did arrive
      setPending(null);
      state.trackedId = match.id;
      return show('status');
    }
    return show('uncertain');          // unknown: let the patient decide
  }
  const active = newestActive();
  if (active) {
    state.chosen = active.type;
    state.trackedId = active.id;
    return show('status');
  }
  show('menu');
}

// =====================================================================
// Drawing
// =====================================================================
let lastOptionsKey = '';

function setText(element, text) {
  if (element.textContent !== text) element.textContent = text;   // avoids re-announcing
}

function render() {
  if (!state.signedIn) return;
  const screen = state.loaded ? currentScreen()
    : {title: 'Connecting…', detail: 'Loading your requests.', options: [], back: null};
  state.highlight = Math.min(state.highlight, Math.max(0, screen.options.length - 1));

  setText($('title'), screen.title);
  setText($('detail'), screen.detail);
  document.body.dataset.screen = state.loaded ? state.screen : 'loading';  // CSS uses this for layout

  // Sent → Seen → Done progress bar (status screen only).
  $('steps').hidden = screen.step === undefined;
  [...$('steps').children].forEach((li, i) => {
    li.classList.toggle('done', i <= (screen.step ?? -1));
  });

  // Rebuild the option buttons only when the options themselves change.
  // (Rebuilding every second would swallow mouse clicks.)
  const options = $('options');
  const key = state.screen + JSON.stringify(screen.options.map(o => [o.icon, o.label, o.note]));
  if (key !== lastOptionsKey) {
    lastOptionsKey = key;
    options.replaceChildren(...screen.options.map((option, index) => {
      const button = document.createElement('button');
      button.className = 'option';
      button.dataset.index = index;
      button.tabIndex = -1;          // never focused: input.js handles keys
      const icon = document.createElement('span');
      icon.className = 'icon';
      icon.textContent = option.icon;
      icon.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.className = 'label';
      label.textContent = option.label;
      button.append(icon, label);
      if (option.note) {
        const note = document.createElement('span');
        note.className = 'note';
        note.textContent = option.note;
        button.append(note);
      }
      return button;
    }));
  }
  // Move the highlight (big yellow box + ▶ marker).
  [...options.children].forEach((button, i) => {
    const on = i === state.highlight;
    button.classList.toggle('highlighted', on);
    if (on) button.setAttribute('aria-current', 'true');
    else button.removeAttribute('aria-current');
  });

  renderDevice();
}

// The small line at the bottom about the ESP32 controller.
function renderDevice() {
  const messages = {
    ready: state.deviceConnected ? '🎮 ESP32 controller connected'
                                 : '🎮 ESP32 controller not connected — keyboard and mouse still work',
    starting: 'Starting device controls…',
    busy: '⚠ Another patient screen is using the ESP32 controls. Close the other tab or window.',
    paused: 'Device controls paused while this tab is hidden.',
    offline: 'Device controls reconnecting…',
    'signed-out': '',
  };
  setText($('device'), messages[state.device] ?? '');
}

// Mouse: one listener for all option buttons.
$('options').addEventListener('mousedown', event => event.preventDefault());  // don't take focus
$('options').addEventListener('click', event => {
  const button = event.target.closest('button[data-index]');
  if (button) input.click(Number(button.dataset.index));
});

// =====================================================================
// Start / stop with sign-in
// =====================================================================
const session = createSession(api, 'patient', {
  onSignIn() {
    state.signedIn = true;
    state.loaded = false;           // forces recoverAfterLoad() on first poll
    render();
    stopRequestPolling = startPolling(refresh, POLL_MS);
    stopDevicePolling = startDevicePolling(api, input, status => {
      state.device = status;
      renderDevice();
    });
  },
  onSignOut() {
    state.signedIn = false;
    clearTimeout(returnTimer);
    stopRequestPolling?.();
    stopDevicePolling?.();
  },
});
