import {useSyncExternalStore} from 'react';

// The patient/staff logic lives in plain-JS controllers (no React inside),
// so it stays easy to test and reason about. A controller calls emit()
// whenever its state changes; components call useStore() to re-render.
export function createStore() {
  const listeners = new Set();
  let version = 0;
  return {
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getVersion: () => version,
    emit() {
      version += 1;
      listeners.forEach(listener => listener());
    },
  };
}

export function useStore(store) {
  return useSyncExternalStore(store.subscribe, store.getVersion);
}

export function startPolling(fn, ms) {
  let stopped = false;
  (async () => {
    while (!stopped) {
      try { await fn(); } catch (error) { console.error(error); }
      await new Promise(resolve => setTimeout(resolve, ms));
    }
  })();
  return () => { stopped = true; };
}
