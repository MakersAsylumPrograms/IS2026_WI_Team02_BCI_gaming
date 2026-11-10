// =====================================================================
// input.js — turns EVERY kind of input into a small set of commands.
//
//   keyboard ─┐
//   mouse ────┼──>  NEXT / PREVIOUS / SELECT / BACK  ──>  handleCommand()
//   ESP32 ────┘     (joystick now, EOG eye gestures later)
//
// The screen code never listens to keys or clicks itself. It only
// receives commands. That way the joystick, the EOG headset and the
// keyboard all behave exactly the same, and adding a new input source
// never touches the UI code.
//
// Important: the whole patient flow must work with ONLY NEXT + SELECT,
// because EOG may give us just two reliable gestures. PREVIOUS and BACK
// are shortcuts, never the only way to do something.
// =====================================================================
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
export const SELECT_DEBOUNCE_MS = 700;

// ---------------------------------------------------------------------
// createInput(handleCommand)
//
// handleCommand(command, detail) is called for every accepted command.
//   detail.source = 'keyboard' | 'mouse' | 'joystick' | 'eog'
//   detail.index  = (mouse only) which option was clicked
//
// Returns an object with:
//   click(index)      -> call this from a click on option number `index`
//   fromDevice(event) -> call this with an event from the ESP32
// ---------------------------------------------------------------------
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
  document.addEventListener('keydown', event => {
    // Let people type normally in the sign-in box, and keep browser
    // shortcuts (Ctrl+R etc.) working.
    const tag = event.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (event.ctrlKey || event.altKey || event.metaKey) return;

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
  });

  return {
    // ---- Mouse ----
    // A click on option `index` means "highlight it, then SELECT it".
    // The screen code moves its highlight to detail.index first, so the
    // mouse and keyboard always agree on what is highlighted.
    click(index) {
      dispatch(SELECT, {source: 'mouse', index});
    },

    // =================================================================
    // ESP32 HOOK  ← hardware commands enter the UI here
    // =================================================================
    // The ESP32 (joystick now, EOG later) never talks to the browser
    // directly. The path is:
    //
    //   ESP32 --POST /api/v1/input-events--> backend --GET (polled)--> browser
    //
    // startDevicePolling() below fetches those events and calls this
    // function for each one. An event looks like:
    //   {"source":"joystick","command":"NEXT","seq":1, ...}
    //
    // The firmware side lives in Code/firmware/ (EqualCareTransport.emit).
    // Nothing here needs to change when EOG replaces the joystick: EOG
    // just sends the same commands with "source":"eog".
    fromDevice(event) {
      if (![NEXT, PREVIOUS, SELECT].includes(event.command)) return;  // ignore unknown
      dispatch(event.command, {source: event.source});
    },
  };
}

// ---------------------------------------------------------------------
// startDevicePolling(api, input, onStatus)
//
// Asks the backend every 250 ms for new ESP32 commands and feeds them to
// input.fromDevice(). Rules come from Documentation/INTEGRATION.md:
//   - Each page gets its own random "consumer ID". Only ONE patient
//     screen may read device commands at a time (others get CONSUMER_BUSY).
//   - After any error, refresh or hidden tab, start over WITHOUT a cursor
//     so old, stale commands are never replayed.
//
// onStatus(text) is told what is going on: 'ready', 'starting', 'busy',
// 'paused', 'offline' or 'signed-out'.
// Returns a stop() function.
// ---------------------------------------------------------------------
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
      await sleep(250);   // wait between polls; polls never overlap
    }
  }
  loop();

  return function stop() {
    stopped = true;
    document.removeEventListener('visibilitychange', forget);
  };
}
