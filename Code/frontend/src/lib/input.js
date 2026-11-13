import {newId} from './api.js';

export const NEXT = 'NEXT';
export const PREVIOUS = 'PREVIOUS';
export const SELECT = 'SELECT';
export const BACK = 'BACK';

// Keyboard mapping (event.key -> command).
const KEY_MAP = {
  ArrowRight: NEXT, ArrowDown: NEXT,
  ArrowLeft: PREVIOUS, ArrowUp: PREVIOUS,
  Enter: SELECT, ' ': SELECT,
  Escape: BACK,
};

// Two SELECTs closer together than this count as one. This stops a
// double-press, double-click or a noisy gesture from submitting twice.
const SELECT_DEBOUNCE_MS = 700;

// Device input (eyes / joystick) is fetched this often while the patient tab is visible.
// The backend is on the same laptop or LAN, so 20 small requests a second are cheap and
// keep the highlight within ~50 ms of the gesture (it was 250 ms, which felt laggy).
const POLL_MS = 50;

export function createInput(handleCommand) {
  let lastSelectAt = -Infinity;

  // Every source ends up here: one place for the debounce rule.
  function dispatch(command, detail) {
    if (command === SELECT) {
      const now = performance.now();
      if (now - lastSelectAt < SELECT_DEBOUNCE_MS) return;   // too soon: ignore
      lastSelectAt = now;
    }
    handleCommand(command, detail);
  }

  // ---- Keyboard ----
  function onKeyDown(event) {
    // Let people type normally in the sign-in box, and keep browser
    // shortcuts (Ctrl+R etc.) working.
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (event.ctrlKey || event.altKey || event.metaKey) return;
    // A normal button the caregiver tabbed to (e.g. "Sign out") keeps its
    // usual Enter/Space behaviour. Menu options are never focused, so
    // this never applies to them.
    if ((tag === 'BUTTON' || tag === 'A') && !event.target.hasAttribute('data-index')) return;

    const command = KEY_MAP[event.key];
    if (!command) return;

    // Always block the browser's default action (scrolling with Space or
    // arrows, Enter "clicking" a focused button). Otherwise the browser
    // and our command layer could BOTH react to one key press.
    event.preventDefault();

    // A held-down key fires repeated keydown events with event.repeat=true.
    // Ignore those: one physical press = one command.
    if (event.repeat) return;

    dispatch(command, {source: 'keyboard'});
  }
  document.addEventListener('keydown', onKeyDown);

  return {
    dispose() {
      document.removeEventListener('keydown', onKeyDown);
    },

    click(index) {
      dispatch(SELECT, {source: 'mouse', index});
    },

    fromDevice(event) {
      if (![NEXT, PREVIOUS, SELECT].includes(event.command)) return;  // ignore unknown
      dispatch(event.command, {source: event.source});
    },
  };
}

export function startDevicePolling(api, input, onStatus) {
  const consumer = newId();
  let cursor = null;     // last event number we've seen (null = start fresh)
  let epoch = '';        // changes when the backend restarts
  let busySince = null;  // when we first got CONSUMER_BUSY
  let stopped = false;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const forget = () => { cursor = null; };
  document.addEventListener('visibilitychange', forget);

  async function loop() {
    while (!stopped) {
      // Hidden tab: don't hold the device lease, and don't act on commands
      // the patient can't see.
      if (document.hidden) {
        cursor = null;
        onStatus('paused');
        await sleep(250);
        continue;
      }
      try {
        const result = await api.inputs(consumer, cursor, epoch);
        if (stopped) break;
        if (document.hidden) { cursor = null; continue; }
        cursor = result.cursor;   // save position BEFORE acting on events
        epoch = result.epoch;
        busySince = null;
        onStatus('ready');
        for (const event of result.events) input.fromDevice(event);
      } catch (error) {
        cursor = null;
        if (error.code === 'CONSUMER_BUSY') {
          // Right after a page refresh the OLD page still holds the lease
          // for up to 3 s. That's normal, so only warn if it lasts longer.
          busySince ??= Date.now();
          onStatus(Date.now() - busySince > 4000 ? 'busy' : 'starting');
        } else {
          onStatus(error.status === 401 ? 'signed-out' : 'offline');
        }
      }
      await sleep(POLL_MS);   // wait between polls; polls never overlap
    }
  }
  loop();

  return function stop() {
    stopped = true;
    document.removeEventListener('visibilitychange', forget);
  };
}
