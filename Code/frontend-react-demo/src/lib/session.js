import {createStore} from './store.js';

// Connection indicator shared by both screens ("Connected" / "Reconnecting…").
export function createConnection() {
  const store = createStore();
  const state = {checked: false, online: true, lastOkAt: Date.now()};
  return {
    store,
    state,
    ok() {
      const changed = !state.checked || !state.online;
      state.checked = true;
      state.online = true;
      state.lastOkAt = Date.now();
      if (changed) store.emit();
    },
    failed() {
      state.checked = true;
      state.online = false;
      store.emit();   // emit every time so "last update N s ago" keeps counting
    },
    secondsSinceOk: () => Math.round((Date.now() - state.lastOkAt) / 1000),
  };
}

// Sign in / sign out / restore-after-refresh for one role ('patient' | 'staff').
//   phase: 'restoring' (asking the backend) | 'out' (show login) | 'in' (show app)
// connection (optional) is told whenever the backend answers or doesn't, so
// the pill is right on the sign-in screen too, before any polling starts.
export function createSession(api, role, {onSignIn, onSignOut, connection}) {
  const store = createStore();
  const state = {
    phase: 'restoring',
    signedIn: false,
    message: '',                          // under the login form
    status: 'Restoring your session…',    // general status line (role="status")
    submitting: false,
  };

  function show(isSignedIn, text = '') {
    state.status = '';
    state.phase = isSignedIn ? 'in' : 'out';
    state.message = text;
    if (isSignedIn && !state.signedIn) { state.signedIn = true; onSignIn(); }
    if (!isSignedIn && state.signedIn) { state.signedIn = false; onSignOut(); }
    // After signing in, move focus off the password box, otherwise input.js
    // would treat arrow keys as typing and ignore them.
    if (isSignedIn) document.activeElement?.blur?.();
    store.emit();
  }

  async function login(password, loginId) {
    state.submitting = true;
    state.message = 'Signing in…';
    store.emit();
    try {
      await api.login(role, password, loginId?.trim());
      state.submitting = false;
      show(true);
      return true;
    } catch (error) {
      state.submitting = false;
      state.message = error.status === 401 ? 'Incorrect login details. Try again.'
        : error.offline ? 'Cannot reach the EqualCare server. Check the address and Wi-Fi.'
        : error.message;
      store.emit();
      return false;
    }
  }

  async function logout() {
    try {
      await api.logout();
      show(false, 'Signed out.');
    } catch (error) {
      if (error.status === 401) show(false, 'Your session has ended.');
      else {
        state.status = 'Could not confirm sign out. Check the connection and try again.';
        store.emit();
      }
    }
  }

  // On page load: are we still signed in? If the server is unreachable,
  // keep retrying every 2 s so the screen recovers by itself.
  (async function restore() {
    try {
      // The backend answers for THIS screen's role only (api sends it), so the
      // other screen being signed in, in another tab, does not count here.
      const current = await api.session();
      connection?.ok();
      show(current.role === role);
    } catch (error) {
      if (error.status !== 401) {
        connection?.failed();
        state.status = 'Cannot restore your session yet. Reconnecting…';
        store.emit();
        setTimeout(restore, 2000);
      } else {
        connection?.ok();   // the server answered
        show(false);   // 401: simply not signed in yet
      }
    }
  })();

  return {
    store,
    state,
    login,
    logout,
    expire(text = 'Your session ended. Please sign in again.') { show(false, text); },
  };
}
