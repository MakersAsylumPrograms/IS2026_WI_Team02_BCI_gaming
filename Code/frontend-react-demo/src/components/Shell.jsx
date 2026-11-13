import {useState} from 'react';
import {BedSingle, LoaderCircle, LogIn, LogOut, Stethoscope, WifiOff} from 'lucide-react';
import {useStore} from '../lib/store.js';
import {Brand, Card, DemoBanner, Disclaimer} from './ui.jsx';

// Page frame shared by the patient and staff screens:
// top bar, offline banner, sign-in card, and the app once signed in.
export default function Shell({role, session, connection, children}) {
  useStore(session.store);
  const {phase, status} = session.state;
  const signedIn = phase === 'in';

  return (
    <div className="flex min-h-screen flex-col">
      <DemoBanner />
      <TopBar role={role} connection={connection} onLogout={signedIn ? session.logout : null} />
      <OfflineBanner connection={connection} />

      <main className="flex flex-1 flex-col">
        {phase === 'restoring' ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-4 px-4 py-24 text-slate-500">
            <LoaderCircle className="size-8 animate-spin text-teal-600" aria-hidden="true" />
            <p role="status" className="text-base font-medium">{status}</p>
          </div>
        ) : status ? (
          <p role="status" className="mx-auto mt-4 w-full max-w-7xl px-4 sm:px-6">
            <span className="block rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm font-medium text-amber-900">
              {status}
            </span>
          </p>
        ) : null}

        {phase === 'out' && <LoginCard role={role} session={session} />}
        {signedIn && children}
      </main>

      {role !== 'patient' && <Disclaimer />}
    </div>
  );
}

function TopBar({role, connection, onLogout}) {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/60 bg-white/75 backdrop-blur-lg">
      <div className="mx-auto flex h-16 max-w-7xl items-center gap-3 px-4 sm:px-6">
        <Brand
          subtitle={role === 'patient' ? 'Patient bedside screen' : 'Nurse station'}
          href={role === 'patient' ? undefined : '/'}
        />
        <div className="ml-auto flex items-center gap-2 sm:gap-3">
          <ConnectionPill connection={connection} />
          {onLogout && (
            <button
              id="logout"
              type="button"
              onClick={onLogout}
              // Don't take focus on click: on the patient screen a focused
              // button would swallow the arrow keys.
              onMouseDown={event => event.preventDefault()}
              className="inline-flex items-center gap-2 rounded-full px-3 py-2 text-sm font-medium text-slate-500 transition hover:bg-slate-100 hover:text-slate-900"
            >
              <LogOut className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">Sign out</span>
              <span className="sr-only sm:hidden">Sign out</span>
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

function ConnectionPill({connection}) {
  useStore(connection.store);
  const {checked, online} = connection.state;
  const look = !checked
    ? {text: 'Connecting…', pill: 'bg-slate-100 text-slate-600 ring-slate-200', dot: 'bg-slate-400 animate-pulse'}
    : online
      ? {text: 'Connected', pill: 'bg-emerald-50 text-emerald-800 ring-emerald-200', dot: 'bg-emerald-500'}
      : {text: 'Reconnecting…', pill: 'bg-rose-50 text-rose-800 ring-rose-200', dot: 'bg-rose-500 animate-pulse'};
  return (
    <span id="connection" className={`pill inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-sm font-semibold ring-1 ${look.pill}`}>
      <span className={`size-2 rounded-full ${look.dot}`} aria-hidden="true" />
      {look.text}
    </span>
  );
}

function OfflineBanner({connection}) {
  useStore(connection.store);
  if (!connection.state.checked || connection.state.online) return null;
  return (
    <div id="offline-banner" role="alert" className="border-b border-rose-200 bg-rose-50 text-rose-900">
      <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3 text-sm font-medium sm:px-6">
        <WifiOff className="size-5 shrink-0" aria-hidden="true" />
        <span>
          Cannot reach the EqualCare server. Reconnecting… (last update {connection.secondsSinceOk()} s ago —
          information may be out of date)
        </span>
      </div>
    </div>
  );
}

function LoginCard({role, session}) {
  const staff = role === 'staff';
  const {message, submitting} = session.state;
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const Icon = staff ? Stethoscope : BedSingle;
  const isError = /incorrect|cannot|ended|before/i.test(message);

  async function submit(event) {
    event.preventDefault();
    if (await session.login(password, loginId)) setPassword('');
  }

  return (
    <div className="mx-auto w-full max-w-md px-4 py-12 motion-safe:animate-rise sm:py-20">
      <Card pad="p-8" className="shadow-xl shadow-teal-900/5">
        <span className="grid size-14 place-items-center rounded-2xl bg-teal-50 text-teal-700 ring-1 ring-teal-100">
          <Icon className="size-7" aria-hidden="true" />
        </span>
        <h1 className="mt-5 text-2xl font-bold tracking-tight text-slate-900">
          {staff ? 'Nurse sign in' : 'Set up the patient screen'}
        </h1>
        <p className="mt-1.5 text-sm text-slate-500">
          {staff
            ? 'See live requests from the bedside and respond in one tap.'
            : 'A caregiver signs in once on this device. It stays signed in after a refresh.'}
        </p>

        <form id="login" onSubmit={submit} className="mt-6 space-y-4">
          {staff && (
            <Field id="login-id" label="Staff login ID" autoComplete="username" autoFocus
                   value={loginId} onChange={setLoginId} />
          )}
          <Field id="password" type="password" autoComplete="current-password" autoFocus={!staff}
                 label={staff ? 'Staff password' : 'Patient password'}
                 hint="Demo: any password works"
                 value={password} onChange={setPassword} />
          <button
            type="submit"
            disabled={submitting}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-teal-600 px-4 py-3 text-base font-semibold text-white shadow-lg shadow-teal-600/25 transition hover:bg-teal-700 active:scale-[0.99] disabled:cursor-wait disabled:opacity-70"
          >
            {submitting ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
                        : <LogIn className="size-5" aria-hidden="true" />}
            Sign in
          </button>
          <p id="login-message" role="status"
             className={`min-h-5 text-center text-sm font-medium ${isError ? 'text-rose-700' : 'text-slate-500'}`}>
            {message}
          </p>
        </form>
      </Card>
    </div>
  );
}

function Field({id, label, hint, value, onChange, type = 'text', ...rest}) {
  return (
    <div>
      <label htmlFor={id} className="flex items-baseline justify-between text-sm font-semibold text-slate-700">
        {label}
        {hint && <small className="text-xs font-normal text-slate-400">{hint}</small>}
      </label>
      <input
        id={id}
        type={type}
        required
        value={value}
        onChange={event => onChange(event.target.value)}
        className="mt-1.5 block w-full rounded-xl border border-slate-200 bg-white px-4 py-3 text-base text-slate-900 shadow-inner shadow-slate-900/[0.02] transition placeholder:text-slate-400 focus:border-teal-500 focus:outline-none focus:ring-4 focus:ring-teal-500/15"
        {...rest}
      />
    </div>
  );
}
