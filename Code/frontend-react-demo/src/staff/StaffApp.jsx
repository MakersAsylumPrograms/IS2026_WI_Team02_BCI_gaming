import {
  Activity, BedSingle, BellOff, BellRing, Check, CircleCheck, Clock, Eye, History, Keyboard,
  Leaf, LoaderCircle, Timer, TriangleAlert, Volume2, X,
} from 'lucide-react';
import Shell from '../components/Shell.jsx';
import {Card, Kbd} from '../components/ui.jsx';
import {useStore} from '../lib/store.js';
import {greeting, visualFor} from '../lib/visuals.js';

export default function StaffApp({app}) {
  return (
    <Shell role="staff" session={app.session} connection={app.connection}>
      <Dashboard app={app} />
    </Shell>
  );
}

function Dashboard({app}) {
  useStore(app.store);
  const view = app.view();

  return (
    <section id="app" className="mx-auto w-full max-w-7xl px-4 pb-10 pt-6 sm:px-6 lg:pt-8">
      <AlertToast alert={view.alert} onClose={app.dismissAlert} />
      <Header view={view} />
      <Stats view={view} />

      <p id="notice" role="status" className="empty:hidden mt-5 flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-medium text-rose-800">
        {view.notice && <><TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />{view.notice}</>}
      </p>

      <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <ActiveQueue app={app} view={view} />
        <aside className="space-y-6">
          <PatientCard view={view} />
          <SoundCard app={app} soundOn={view.soundOn} />
          <HistoryCard view={view} />
          <ShortcutsCard />
        </aside>
      </div>
    </section>
  );
}

function Header({view}) {
  const now = new Date();
  const date = now.toLocaleDateString([], {weekday: 'long', day: 'numeric', month: 'long'});
  const time = now.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        <p className="text-sm font-semibold text-teal-700">{date}</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">{greeting(now)}, care team</h1>
        <p className="mt-1 text-slate-500">Here's what {view.patientName} needs right now.</p>
      </div>
      <p className="flex items-center gap-2 rounded-2xl bg-white/80 px-4 py-2 text-2xl font-semibold tabular-nums text-slate-700 shadow-sm ring-1 ring-slate-200/70">
        <Clock className="size-5 text-teal-600" aria-hidden="true" />
        {time}
      </p>
    </div>
  );
}

// ---- Stat tiles -------------------------------------------------------
const TONES = {
  amber:   {tile: 'bg-amber-50 text-amber-600 ring-amber-100',     value: 'text-amber-950'},
  rose:    {tile: 'bg-rose-50 text-rose-600 ring-rose-100',        value: 'text-rose-900'},
  sky:     {tile: 'bg-sky-50 text-sky-600 ring-sky-100',           value: 'text-sky-950'},
  emerald: {tile: 'bg-emerald-50 text-emerald-600 ring-emerald-100', value: 'text-emerald-950'},
  teal:    {tile: 'bg-teal-50 text-teal-600 ring-teal-100',        value: 'text-teal-950'},
};

function Stats({view}) {
  const waitingTone = view.overdue ? 'rose' : 'amber';
  return (
    <div className="mt-6 grid grid-cols-2 gap-3 sm:gap-4 lg:grid-cols-4">
      <Stat icon={view.overdue ? TriangleAlert : BellRing} tone={waitingTone} label="Waiting"
            value={view.loaded ? view.waiting : '–'}
            hint={view.overdue ? `${view.overdue} overdue` : 'Not yet seen'} />
      <Stat icon={Activity} tone="sky" label="In progress" value={view.loaded ? view.inProgress : '–'} hint="Seen, not done" />
      <Stat icon={CircleCheck} tone="emerald" label="Resolved" value={view.loaded ? view.resolved.length : '–'} hint="Completed requests" />
      <Stat icon={Timer} tone="teal" label="Avg. time to seen" value={view.avgSeen ?? '–'} hint="Across all requests" />
    </div>
  );
}

function Stat({icon: Icon, tone, label, value, hint}) {
  const t = TONES[tone];
  return (
    <Card pad="p-4 sm:p-5" className="flex items-start gap-3 sm:gap-4">
      <span className={`grid size-11 shrink-0 place-items-center rounded-xl ring-1 ${t.tile}`}>
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-500">{label}</p>
        <p className={`mt-0.5 truncate text-2xl font-bold tabular-nums tracking-tight sm:text-3xl ${t.value}`}>{value}</p>
        <p className="mt-0.5 text-xs text-slate-400">{hint}</p>
      </div>
    </Card>
  );
}

// ---- Active queue -----------------------------------------------------
function ActiveQueue({app, view}) {
  return (
    <div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold tracking-tight text-slate-900">Needs attention</h2>
          <p className="text-sm text-slate-500">Waiting requests first, oldest at the top.</p>
        </div>
        <span id="summary" aria-live="polite"
              className={`summary rounded-full px-3 py-1 text-sm font-semibold ring-1 ${
                view.waiting ? 'bg-amber-50 text-amber-900 ring-amber-200' : 'bg-white text-slate-600 ring-slate-200'}`}>
          {view.loaded ? view.summary : 'Loading…'}
        </span>
      </div>

      <div id="active" className="mt-4 space-y-3">
        {view.active.map(row => (
          <RequestCard key={row.id} row={row} patientName={view.patientName} onAct={() => app.act(row.id)} />
        ))}
      </div>

      {!view.loaded && <Skeleton />}
      {view.loaded && view.active.length === 0 && (
        <div id="active-empty" className="empty mt-4 flex flex-col items-center rounded-3xl border-2 border-dashed border-emerald-200 bg-emerald-50/50 px-6 py-14 text-center motion-safe:animate-rise">
          <span className="grid size-16 place-items-center rounded-full bg-emerald-100 text-emerald-600">
            <Leaf className="size-8" aria-hidden="true" />
          </span>
          <p className="mt-4 text-lg font-semibold text-emerald-900">All clear — no active requests.</p>
          <p className="mt-1 text-sm text-emerald-800/70">
            New requests appear here instantly{view.soundOn ? ', with a chime' : ''}.
          </p>
        </div>
      )}
    </div>
  );
}

const STATUS_LOOK = {
  PENDING:      {badge: 'Waiting', badgeClass: 'bg-amber-100 text-amber-800',     bar: 'bg-amber-400',   card: 'border-amber-200/80', wait: 'text-amber-700'},
  OVERDUE:      {badge: 'Overdue', badgeClass: 'bg-rose-100 text-rose-700',       bar: 'bg-rose-500',    card: 'border-rose-200 bg-rose-50/60', wait: 'text-rose-700 font-semibold'},
  ACKNOWLEDGED: {badge: 'Seen',    badgeClass: 'bg-sky-100 text-sky-800',         bar: 'bg-sky-400',     card: 'border-sky-100', wait: 'text-sky-700'},
  RESOLVED:     {badge: 'Done',    badgeClass: 'bg-emerald-100 text-emerald-800', bar: 'bg-emerald-400', card: 'border-slate-100', wait: 'text-slate-500'},
};

function RequestCard({row, patientName, onAct}) {
  const {Icon, tile} = visualFor(row.type);
  const look = STATUS_LOOK[row.overdue ? 'OVERDUE' : row.status];
  const classes = ['request', row.status.toLowerCase(), row.overdue && 'overdue', row.glowing && 'new']
    .filter(Boolean).join(' ');
  return (
    <article className={`${classes} relative flex flex-wrap items-center gap-4 overflow-hidden rounded-2xl border bg-white py-4 pl-6 pr-4 shadow-sm transition-colors sm:flex-nowrap sm:pr-5 ${look.card} ${
      row.glowing ? 'ring-2 ring-teal-300 motion-safe:animate-glow' : ''}`}>
      <span aria-hidden="true" className={`absolute inset-y-0 left-0 w-1.5 ${look.bar}`} />
      <span aria-hidden="true" className={`icon grid size-14 shrink-0 place-items-center rounded-2xl ${tile}`}>
        <Icon className="size-7" />
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="type text-lg font-semibold text-slate-900">{row.label}</h3>
          <span className={`badge rounded-full px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide ${look.badgeClass}`}>{look.badge}</span>
          {row.glowing && <span className="rounded-full bg-teal-600 px-2 py-0.5 text-xs font-bold uppercase tracking-wide text-white">New</span>}
        </div>
        <p className="meta mt-1 truncate text-sm text-slate-500" title={`Full request ID: ${row.id}`}>
          {patientName} · requested {row.requestedAt} · ID {row.id.slice(0, 8)}
        </p>
        <p className={`wait mt-1.5 inline-flex items-center gap-1.5 text-sm font-medium ${look.wait}`}>
          {row.overdue ? <TriangleAlert className="size-4" aria-hidden="true" /> : <Clock className="size-4" aria-hidden="true" />}
          {row.wait}
        </p>
      </div>

      {row.action && (
        <button
          type="button"
          onClick={onAct}
          disabled={row.busy}
          aria-label={row.actionLabel}
          className={`action inline-flex w-full items-center justify-center gap-2 rounded-xl px-6 py-3 text-base font-semibold text-white shadow-md transition active:scale-[0.98] disabled:cursor-wait disabled:opacity-70 sm:w-auto sm:min-w-32 ${
            row.status === 'PENDING' ? 'bg-teal-600 shadow-teal-600/25 hover:bg-teal-700'
                                     : 'bg-emerald-600 shadow-emerald-600/25 hover:bg-emerald-700'}`}
        >
          {row.busy ? <LoaderCircle className="size-5 animate-spin" aria-hidden="true" />
            : row.status === 'PENDING' ? <Eye className="size-5" aria-hidden="true" />
            : <Check className="size-5" aria-hidden="true" />}
          {row.action}
        </button>
      )}
    </article>
  );
}

function Skeleton() {
  return (
    <div className="mt-4 space-y-3" aria-hidden="true">
      {[0, 1].map(i => (
        <div key={i} className="flex animate-pulse items-center gap-4 rounded-2xl border border-slate-100 bg-white p-5">
          <span className="size-14 rounded-2xl bg-slate-100" />
          <span className="flex-1 space-y-2">
            <span className="block h-4 w-1/3 rounded bg-slate-100" />
            <span className="block h-3 w-2/3 rounded bg-slate-100" />
          </span>
        </div>
      ))}
    </div>
  );
}

// ---- New-request alert ------------------------------------------------
function AlertToast({alert, onClose}) {
  if (!alert) return null;
  return (
    // Pinned under the top bar, top-right on wide screens so it never covers the queue.
    <div className="pointer-events-none fixed inset-x-0 top-28 z-40 mx-auto flex max-w-7xl justify-center px-4 sm:justify-end sm:px-6">
      <div key={alert.id} id="alert-banner" role="alert"
           className="banner alert pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-2xl border border-amber-200 bg-amber-50/95 p-3 pl-4 text-amber-950 shadow-2xl shadow-amber-900/15 backdrop-blur motion-safe:animate-toast-in">
        <span className="grid size-10 shrink-0 place-items-center rounded-full bg-amber-400 text-white shadow">
          <BellRing className="size-5 motion-safe:animate-ring" aria-hidden="true" />
        </span>
        <p className="flex-1 font-semibold">{alert.text}</p>
        <button type="button" onClick={onClose} aria-label="Dismiss alert"
                className="grid size-9 place-items-center rounded-full text-amber-800 transition hover:bg-amber-100">
          <X className="size-5" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

// ---- Sidebar ----------------------------------------------------------
function PatientCard({view}) {
  const connected = view.deviceConnected;
  const text = connected === null ? ''
    : connected ? `${view.patientName} ESP32 controller: connected`
    : `${view.patientName} ESP32 controller: not connected (patient can still use keyboard/mouse)`;
  return (
    <Card>
      <div className="flex items-center gap-3">
        <span className="grid size-12 place-items-center rounded-2xl bg-teal-50 text-teal-700 ring-1 ring-teal-100">
          <BedSingle className="size-6" aria-hidden="true" />
        </span>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">Patient</p>
          <p className="text-lg font-semibold text-slate-900">{view.patientName}</p>
        </div>
      </div>
      <p id="device" className="device mt-4 flex items-start gap-2.5 rounded-xl bg-slate-50 px-3 py-2.5 text-sm text-slate-600 empty:hidden">
        {text && (
          <>
            <span aria-hidden="true" className={`mt-1.5 size-2 shrink-0 rounded-full ${connected ? 'bg-emerald-500' : 'bg-slate-300'}`} />
            {text}
          </>
        )}
      </p>
    </Card>
  );
}

function SoundCard({app, soundOn}) {
  return (
    <Card>
      <p className="text-sm font-semibold text-slate-900">Sound alerts</p>
      <p className="mt-1 text-xs text-slate-500">Browsers only play sound after one click on the page.</p>
      <button
        id="sound"
        type="button"
        onClick={app.enableSound}
        className={`sound-button mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
          soundOn ? 'on bg-emerald-50 text-emerald-800 ring-1 ring-emerald-200 hover:bg-emerald-100'
                  : 'bg-amber-50 text-amber-900 ring-1 ring-amber-200 hover:bg-amber-100'}`}
      >
        {soundOn ? <Volume2 className="size-4" aria-hidden="true" /> : <BellOff className="size-4" aria-hidden="true" />}
        {soundOn ? 'Sound alerts on — click to test' : 'Click to enable sound alerts'}
      </button>
    </Card>
  );
}

function HistoryCard({view}) {
  return (
    <Card pad="p-0" className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4">
        <History className="size-4 text-slate-400" aria-hidden="true" />
        <h2 id="history-summary" className="text-sm font-semibold text-slate-900">
          Resolved history ({view.resolved.length})
        </h2>
      </div>
      <div id="history" className="history max-h-80 divide-y divide-slate-100 overflow-y-auto">
        {view.resolved.map(row => {
          const {Icon, tile} = visualFor(row.type);
          return (
            <article key={row.id} className="request resolved flex items-center gap-3 px-5 py-3" title={`Full request ID: ${row.id}`}>
              <span aria-hidden="true" className={`grid size-9 shrink-0 place-items-center rounded-xl ${tile}`}>
                <Icon className="size-4" />
              </span>
              <div className="min-w-0">
                <p className="type text-sm font-semibold text-slate-800">{row.label}</p>
                <p className="wait truncate text-xs text-slate-500">{row.wait}</p>
              </div>
            </article>
          );
        })}
      </div>
      {view.resolved.length === 0 && (
        <p id="history-empty" className="empty px-5 py-6 text-center text-sm text-slate-400">No resolved requests yet.</p>
      )}
    </Card>
  );
}

function ShortcutsCard() {
  return (
    <Card>
      <p className="flex items-center gap-2 text-sm font-semibold text-slate-900">
        <Keyboard className="size-4 text-slate-400" aria-hidden="true" />
        Keyboard shortcuts
      </p>
      <ul className="hint mt-3 space-y-2 text-sm text-slate-600">
        <li className="flex items-center gap-3"><Kbd>S</Kbd> Top waiting request is Seen</li>
        <li className="flex items-center gap-3"><Kbd>D</Kbd> Top seen request is Done</li>
      </ul>
    </Card>
  );
}
