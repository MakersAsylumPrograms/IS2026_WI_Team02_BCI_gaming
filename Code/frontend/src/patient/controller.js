import {EqualCareAPI, REQUEST_TYPES, typeInfo, newId} from '../lib/api.js';
import {createInput, startDevicePolling, NEXT, PREVIOUS, SELECT, BACK} from '../lib/input.js';
import {createStore, startPolling} from '../lib/store.js';
import {createConnection, createSession} from '../lib/session.js';

// Patient screen logic. No React in here: PatientApp.jsx draws whatever
// view() returns and re-draws whenever store.emit() runs.

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

// Eye calibration / test (the 'eyes' screen). The input process runs it; we show its cues.
const EYES_POLL_MS = 300;
const EYES_ACTIVE = ['requested', 'running', 'fitting'];
const CUE_TITLE = {
  CENTER: 'Look at the centre', REST: 'Relax', LEFT: '← Look left', RIGHT: 'Look right →',
  BLINK: 'Blink once', DOUBLE_BLINK: 'Blink twice', NATURAL: 'Look around normally',
};
const CUE_DETAIL = {
  CENTER: 'Keep your head still.', REST: 'Eyes on the centre.',
  LEFT: 'Move only your eyes, far to the left, and hold until the next cue.',
  RIGHT: 'Move only your eyes, far to the right, and hold until the next cue.',
  BLINK: 'One firm blink.', DOUBLE_BLINK: 'Two firm blinks, about half a second apart.',
  NATURAL: 'Use the screen as you normally would. No commands should appear.',
};

const DEVICE_TEXT = {
  starting: 'Starting device controls…',
  busy: 'Another patient screen is using the ESP32 controls. Close the other tab or window.',
  paused: 'Device controls paused while this tab is hidden.',
  offline: 'Device controls reconnecting…',
  'signed-out': '',
};

export function createPatientApp() {
  const api = new EqualCareAPI('patient');
  const connection = createConnection();
  const store = createStore();

  // ---- State: everything the screen needs to draw itself -------------
  const state = {
    signedIn: false,
    screen: 'menu',    // 'menu' | 'confirm' | 'sending' | 'status' | 'uncertain' | 'eyes'
    highlight: 0,      // which option is highlighted on the current screen
    chosen: null,      // request type picked on the menu, e.g. 'WATER'
    trackedId: null,   // id of the request shown on the status screen
    requests: [],      // latest list from the backend
    loaded: false,     // true once we have the list at least once
    notice: '',        // one-off message shown on the menu (e.g. an error)
    device: 'starting',        // device polling status (see input.js)
    deviceConnected: false,    // is the ESP32 sending heartbeats?
    eyes: null,        // the calibration / test job while the 'eyes' screen is open
  };
  let returnTimer = null;      // timer that goes back to the menu after "Done"
  let eyesTimer = null;        // polls the calibration job while it runs
  let stopRequestPolling = null, stopDevicePolling = null;

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

  function upsert(request) {
    state.requests = state.requests.filter(r => r.id !== request.id).concat(request);
  }

  function show(screen, highlight = 0) {
    clearTimeout(returnTimer);
    returnTimer = null;
    state.screen = screen;
    state.highlight = highlight;
    render();
  }
  function goToMenu() {
    show('menu', Math.max(0, REQUEST_TYPES.findIndex(t => t.type === state.chosen)));
  }

  // ---- Eye calibration and test ---------------------------------------
  async function startEyes(mode) {
    if (!state.signedIn || !state.loaded || state.screen === 'sending') return;
    try {
      const {job} = await api.startCalibration(mode);
      state.eyes = job;
      show('eyes');
      pollEyes();
    } catch (error) {
      if (error.status === 401) return session.expire();
      state.notice = error.code === 'DEVICE_OFFLINE'
        ? 'The eye sensor is not connected. Plug in the board and start EqualCare, then try again.'
        : `Could not start: ${error.message}`;
      goToMenu();
    }
  }

  function stopEyesPolling() {
    clearTimeout(eyesTimer);
    eyesTimer = null;
  }

  async function pollEyes() {
    stopEyesPolling();
    if (state.screen !== 'eyes' || !state.eyes) return;
    try {
      const {job} = await api.calibration();
      if (state.screen !== 'eyes' || !state.eyes) return;
      if (job?.id === state.eyes.id) state.eyes = job;
      if (state.eyes.state === 'cancelled') { state.eyes = null; return goToMenu(); }
      render();
    } catch (error) {
      if (error.status === 401) return session.expire();
    }
    if (EYES_ACTIVE.includes(state.eyes?.state)) eyesTimer = setTimeout(pollEyes, EYES_POLL_MS);
  }

  async function leaveEyes() {
    const job = state.eyes;
    stopEyesPolling();
    state.eyes = null;
    goToMenu();
    if (job && EYES_ACTIVE.includes(job.state)) {
      try { await api.cancelCalibration(); } catch { /* it times out on its own */ }
    }
  }

  function eyesScreen() {
    const job = state.eyes;
    const test = job.mode === 'test';
    const base = {back: leaveEyes, eyes: job, options: []};
    if (job.state === 'requested') {
      return {...base, title: test ? 'Starting the eye test…' : 'Starting calibration…',
              detail: 'Waiting for the eye sensor.'};
    }
    if (job.state === 'fitting') {
      return {...base, title: 'Working out your settings…', detail: 'This takes a few seconds.'};
    }
    if (job.state === 'running' && test) {
      return {...base, title: 'Try each eye signal',
              detail: 'Look left, look right, blink once, blink twice. Each one appears below. Nothing is sent.'};
    }
    if (job.state === 'running') {
      return {...base, title: CUE_TITLE[job.label] ?? job.cue ?? '',
              detail: CUE_DETAIL[job.label] ?? '', cue: job.label};
    }
    const menu = {icon: 'BACK', label: 'Back to menu', run: leaveEyes};
    if (job.state === 'failed') {
      return {...base, title: test ? 'Eye test stopped' : 'Calibration stopped', detail: job.message || '',
              warning: true, options: [{icon: 'RETRY', label: 'Try again', run: () => startEyes(job.mode)}, menu]};
    }
    if (test) {
      return {...base, title: 'Eye test finished',
              detail: job.signals?.length ? 'These are the signals the sensor picked up.' : 'No eye signals were picked up.',
              options: [{icon: 'EYES', label: 'Calibrate eyes', run: () => startEyes('calibrate')},
                        {icon: 'RETRY', label: 'Test again', run: () => startEyes('test')}, menu]};
    }
    const result = job.result ?? {};
    const why = (result.shortfalls ?? []).join('; ');
    return {...base,
      title: result.passed ? 'Calibration approved ✓' : 'Calibration not approved',
      detail: result.passed ? 'Your eyes now control the screen with these settings.'
        : `The previous settings stay in use. ${why.charAt(0).toUpperCase()}${why.slice(1)}.`,
      warning: !result.passed,
      options: [{icon: 'EYES', label: 'Test my eyes', run: () => startEyes('test')},
                {icon: 'RETRY', label: 'Calibrate again', run: () => startEyes('calibrate')}, menu]};
  }

  // SCREENS — each returns {title, detail, options, back, ...}
  //   options: [{icon, label, note, run}]  (run() is called on SELECT)
  //   icon is a key the view turns into a picture (a request type, CONFIRM, …)
  function currentScreen() {
    const chosen = typeInfo(state.chosen);

    if (state.screen === 'menu') {
      return {
        title: 'How can we help?',
        detail: state.notice || 'Move to what you need, then select it.',
        warning: Boolean(state.notice),
        options: REQUEST_TYPES.map(t => {
          const active = activeRequestOf(t.type);
          return {icon: t.type, label: t.label, note: active ? STATUS_TEXT[active.status].short : '',
                  run: () => chooseType(t.type)};
        }),
        back: null,
      };
    }

    if (state.screen === 'confirm') {
      return {
        title: `${chosen.label}?`,
        detail: 'Send this request to the nurse?',
        type: state.chosen,
        options: [
          {icon: 'CONFIRM', label: 'Confirm', run: () => send(state.chosen)},
          {icon: 'CANCEL',  label: 'Cancel',  run: goToMenu},
        ],
        back: goToMenu,
      };
    }

    if (state.screen === 'eyes' && state.eyes) return eyesScreen();

    if (state.screen === 'sending') {
      return {title: 'Sending…', detail: `Sending your ${chosen.label} request.`, type: state.chosen,
              options: [], back: null};
    }

    if (state.screen === 'uncertain') {
      return {
        title: 'Not sure it was sent',
        detail: `We could not confirm your ${chosen.label} request reached the nurse. ` +
                `Trying again is safe — it will not send a duplicate.`,
        type: state.chosen,
        warning: true,
        options: [
          {icon: 'RETRY', label: 'Try again', run: () => send(state.chosen)},
          {icon: 'CANCEL', label: 'Cancel', run: () => { setPending(null); goToMenu(); }},
        ],
        back: null,   // must choose one: we don't want to silently forget it
      };
    }

    // 'status'
    const request = trackedRequest();
    const info = typeInfo(request?.type ?? state.chosen);
    const status = request?.status ?? 'PENDING';
    const text = STATUS_TEXT[status] || STATUS_TEXT.PENDING;
    return {
      title: text.title,
      detail: `${info.label} — ${text.detail}`,
      type: info.type,
      status,
      options: [{icon: 'BACK', label: 'Back to menu', run: goToMenu}],
      back: goToMenu,
      step: STEP_FOR_STATUS[status] ?? 0,
    };
  }

  // ---- Actions ---------------------------------------------------------
  function chooseType(type) {
    state.chosen = type;
    state.notice = '';
    const active = activeRequestOf(type);
    if (active) {
      // Already asked for this and it's not done: show its status instead.
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
        // No answer, or a temporary server problem: it MAY have been saved.
        show('uncertain');
      } else {
        // The backend clearly said no (4xx): nothing was saved.
        setPending(null);
        state.notice = `Not sent: ${error.message}`;
        goToMenu();
      }
    }
  }

  // handleCommand — THE single entry point for all input.
  function handleCommand(command, detail) {
    if (!state.signedIn || !state.loaded) return;
    const screen = currentScreen();
    const count = screen.options.length;
    // While the eyes are being calibrated or tested, eye and joystick input is the thing being measured.
    if (state.screen === 'eyes' && EYES_ACTIVE.includes(state.eyes?.state) && command !== BACK) return;

    // Mouse click: move the highlight to the clicked option first.
    if (detail.index !== undefined && detail.index < count) state.highlight = detail.index;

    if (command === NEXT && count) state.highlight = (state.highlight + 1) % count;
    if (command === PREVIOUS && count) state.highlight = (state.highlight - 1 + count) % count;
    if (command === BACK && screen.back) return screen.back();
    if (command === SELECT && count) return screen.options[state.highlight].run();
    render();
  }
  const input = createInput(handleCommand);

  // ---- Polling the backend for request status (every POLL_MS) ---------
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

  function render() {
    if (!state.signedIn) return;
    const count = state.loaded ? currentScreen().options.length : 0;
    state.highlight = Math.min(state.highlight, Math.max(0, count - 1));
    store.emit();
  }

  // ---- What the view draws --------------------------------------------
  function view() {
    const screen = state.loaded ? currentScreen()
      : {title: 'Connecting…', detail: 'Loading your requests.', options: [], back: null};
    let device;
    if (state.device === 'ready') {
      device = state.deviceConnected
        ? {tone: 'ok', text: 'ESP32 controller connected'}
        : {tone: 'muted', text: 'ESP32 controller not connected — keyboard and mouse still work'};
    } else {
      device = {tone: state.device === 'busy' ? 'warn' : 'muted', text: DEVICE_TEXT[state.device] ?? ''};
    }
    return {
      ...screen,
      name: state.loaded ? state.screen : 'loading',
      highlight: state.highlight,
      device,
    };
  }

  // ---- Start / stop with sign-in ---------------------------------------
  const session = createSession(api, 'patient', {
    connection,
    onSignIn() {
      state.signedIn = true;
      state.loaded = false;           // forces recoverAfterLoad() on first poll
      render();
      stopRequestPolling = startPolling(refresh, POLL_MS);
      stopDevicePolling = startDevicePolling(api, input, status => {
        state.device = status;
        store.emit();
      });
    },
    onSignOut() {
      state.signedIn = false;
      clearTimeout(returnTimer);
      stopEyesPolling();
      state.eyes = null;
      stopRequestPolling?.();
      stopDevicePolling?.();
    },
  });

  return {
    store,
    session,
    connection,
    view,
    click: index => input.click(index),
    startEyes,
  };
}
