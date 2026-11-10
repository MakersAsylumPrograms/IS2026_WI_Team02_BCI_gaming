// =====================================================================
// common.js — small helpers shared by the patient and staff screens:
//   $()                      shortcut for document.getElementById
//   startPolling()           run a function every N ms, never overlapping
//   createConnectionStatus() the "Connected / Reconnecting…" indicator
//   createSession()          sign-in form, sign-out button, session restore
// =====================================================================

export const $ = id => document.getElementById(id);

// ---------------------------------------------------------------------
// startPolling(fn, ms)
// Calls `await fn()` then waits `ms`, forever, until stop() is called.
// Because it waits for fn to finish first, two polls never overlap even
// if the network is slow. Returns stop().
// ---------------------------------------------------------------------
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

// ---------------------------------------------------------------------
// createConnectionStatus()
// Controls the pill in the top bar (#connection) and the red banner
// (#offline-banner). Call ok() after every successful poll and failed()
// after every failed one.
// ---------------------------------------------------------------------
export function createConnectionStatus() {
  const pill = $('connection');
  const banner = $('offline-banner');
  let lastOkAt = Date.now();

  return {
    ok() {
      lastOkAt = Date.now();
      pill.textContent = '● Connected';
      pill.className = 'pill ok';
      banner.hidden = true;
    },
    failed() {
      const seconds = Math.round((Date.now() - lastOkAt) / 1000);
      pill.textContent = '● Reconnecting…';
      pill.className = 'pill bad';
      banner.hidden = false;
      banner.textContent = `Cannot reach the EqualCare server. Reconnecting… ` +
                           `(last update ${seconds} s ago — information may be out of date)`;
    },
  };
}

// ---------------------------------------------------------------------
// createSession(api, role, {onSignIn, onSignOut})
//
// Handles the sign-in form (#login, #password, #login-message), the
// sign-out button (#logout) and the main area (#app).
//   - On page load it asks the backend whether this browser is already
//     signed in (so a refresh does not ask for the password again).
//   - onSignIn()  is called when the screen should start working.
//   - onSignOut() is called when it should stop (sign out / expired).
// Returns {expire(message)} — call it when a poll gets HTTP 401.
// ---------------------------------------------------------------------
export function createSession(api, role, {onSignIn, onSignOut}) {
  const form = $('login'), password = $('password'), message = $('login-message');
  let signedIn = false;

  function show(isSignedIn, text = '') {
    form.hidden = isSignedIn;
    $('app').hidden = !isSignedIn;
    $('logout').hidden = !isSignedIn;
    message.textContent = text;
    if (isSignedIn && !signedIn) { signedIn = true; onSignIn(); }
    if (!isSignedIn && signedIn) { signedIn = false; onSignOut(); }
    if (!isSignedIn) password.focus();
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    message.textContent = 'Signing in…';
    try {
      await api.login(role, password.value);
      password.value = '';
      show(true);
    } catch (error) {
      message.textContent = error.status === 401 ? 'Wrong password. Try again.'
        : error.offline ? 'Cannot reach the EqualCare server. Check the address and Wi-Fi.'
        : error.message;
    }
  });

  $('logout').addEventListener('click', async () => {
    try { await api.logout(); } catch { /* still sign out locally */ }
    show(false, 'Signed out.');
  });

  // On page load: are we still signed in? If the server is unreachable,
  // keep retrying every 2 s so the screen recovers by itself.
  (async function restore() {
    try {
      const current = await api.session();
      if (current.role === role) return show(true);
      // Same browser is signed in as the OTHER role (cookies are shared
      // between tabs). Signing in here will replace that session.
      show(false, `This browser is signed in as "${current.role}". ` +
                  `Sign in here, or use a private window for the other screen.`);
    } catch (error) {
      if (error.offline) {
        show(false, 'Cannot reach the EqualCare server. Retrying…');
        setTimeout(restore, 2000);
      } else {
        show(false);   // 401: simply not signed in yet
      }
    }
  })();

  return {
    expire(text = 'Your session ended. Please sign in again.') { show(false, text); },
  };
}
