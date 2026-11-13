import {useEffect} from 'react';
import {Check, CircleCheck, Gamepad2, HeartHandshake, Keyboard, LoaderCircle, Send, Sun, TriangleAlert} from 'lucide-react';
import Shell from '../components/Shell.jsx';
import {Kbd} from '../components/ui.jsx';
import {useStore} from '../lib/store.js';
import {greeting, visualFor} from '../lib/visuals.js';

export default function PatientApp({app}) {
  return (
    <Shell role="patient" session={app.session} connection={app.connection}>
      <PatientScreen app={app} />
    </Shell>
  );
}

function PatientScreen({app}) {
  useStore(app.store);
  const view = app.view();

  // Tests (and CSS, if ever needed) read which screen is showing from <body>.
  useEffect(() => { document.body.dataset.screen = view.name; }, [view.name]);
  useEffect(() => () => { delete document.body.dataset.screen; }, []);

  return (
    <section id="app" className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 pb-6 pt-8 sm:px-8 sm:pt-12">
      <div aria-live="polite" className="flex flex-col items-center text-center">
        <div key={view.name} className="flex flex-col items-center motion-safe:animate-rise">
          <Hero view={view} />
          <h1 id="title" className="mt-6 text-balance text-4xl font-bold tracking-tight text-slate-900 sm:text-5xl lg:text-6xl">
            {view.title}
          </h1>
        </div>
        <p id="detail" className={`detail mt-4 max-w-2xl text-pretty text-lg sm:text-2xl ${view.warning ? 'font-medium text-amber-800' : 'text-slate-500'}`}>
          {view.detail}
        </p>
      </div>

      {view.step !== undefined && <Steps step={view.step} />}
      <Options app={app} view={view} />
      <Footer device={view.device} />
    </section>
  );
}

// The big picture above the title, different for each screen.
function Hero({view}) {
  if (view.name === 'menu') {
    return (
      <span className="inline-flex items-center gap-2 rounded-full bg-white/80 px-4 py-2 text-base font-semibold text-teal-800 shadow-sm ring-1 ring-teal-100">
        <Sun className="size-5 text-amber-500" aria-hidden="true" />
        {greeting()}
      </span>
    );
  }
  if (view.name === 'loading') {
    return <Circle className="bg-teal-50 text-teal-600"><LoaderCircle className="size-12 animate-spin" /></Circle>;
  }
  if (view.name === 'uncertain') {
    return <Circle className="bg-amber-100 text-amber-600"><TriangleAlert className="size-12" /></Circle>;
  }
  if (view.name === 'status') {
    const look = {
      PENDING:      {Icon: Send,           circle: 'bg-teal-100 text-teal-700',       pulse: 'bg-teal-300'},
      ACKNOWLEDGED: {Icon: HeartHandshake, circle: 'bg-emerald-100 text-emerald-700', pulse: 'bg-emerald-300'},
      RESOLVED:     {Icon: CircleCheck,    circle: 'bg-emerald-500 text-white',       pulse: null},
    }[view.status] ?? {Icon: Send, circle: 'bg-teal-100 text-teal-700', pulse: 'bg-teal-300'};
    return <Circle className={look.circle} pulse={look.pulse}><look.Icon className="size-12" /></Circle>;
  }
  // confirm / sending: show what was picked
  const {Icon, tile} = visualFor(view.type);
  return (
    <span className={`relative grid size-28 place-items-center rounded-[2rem] shadow-sm ${tile}`} aria-hidden="true">
      <Icon className="size-14" />
      {view.name === 'sending' && (
        <LoaderCircle className="absolute -right-3 -top-3 size-10 animate-spin rounded-full bg-white p-1.5 text-teal-600 shadow" />
      )}
    </span>
  );
}

function Circle({className, pulse, children}) {
  return (
    <span className="relative grid size-28 place-items-center" aria-hidden="true">
      {pulse && <span className={`absolute inset-0 rounded-full motion-safe:animate-breathe ${pulse}`} />}
      <span className={`relative grid size-28 place-items-center rounded-full shadow-sm ${className}`}>{children}</span>
    </span>
  );
}

const STEPS = ['Sent', 'Seen by nurse', 'Done'];

function Steps({step}) {
  return (
    <ol id="steps" aria-label="Request progress" className="steps mx-auto mt-10 flex w-full max-w-2xl items-start">
      {STEPS.map((label, i) => {
        const done = i <= step;
        const last = i === STEPS.length - 1;
        return (
          <li key={label} className={`flex items-start ${last ? '' : 'flex-1'} ${done ? 'done' : ''}`}>
            <div className="flex w-20 flex-col items-center gap-2 text-center sm:w-32">
              <span className={`grid size-12 place-items-center rounded-full border-2 text-lg font-bold transition-colors duration-500 ${
                done ? 'border-emerald-500 bg-emerald-500 text-white shadow-lg shadow-emerald-500/30'
                     : 'border-slate-200 bg-white text-slate-400'}`}>
                {done ? <Check className="size-6" strokeWidth={3} aria-hidden="true" /> : i + 1}
              </span>
              <span className={`text-sm font-semibold sm:text-base ${done ? 'text-emerald-800' : 'text-slate-400'}`}>
                {label}
                {done && <span className="sr-only"> (complete)</span>}
              </span>
            </div>
            {!last && (
              <span aria-hidden="true"
                    className={`mt-5 h-1.5 flex-1 rounded-full transition-colors duration-500 ${i < step ? 'bg-emerald-400' : 'bg-slate-200'}`} />
            )}
          </li>
        );
      })}
    </ol>
  );
}

function Options({app, view}) {
  const count = view.options.length;
  if (!count) return null;
  const big = count >= 4;
  // A single row on laptop screens, so left/right (eyes, joystick, arrows)
  // moves the highlight the way it looks like it should.
  const layout = big ? 'max-w-6xl grid-cols-2 xl:grid-cols-4'
    : count === 2 ? 'max-w-3xl grid-cols-2'
    : 'max-w-sm grid-cols-1';
  return (
    <div id="options" className={`options mx-auto mt-10 grid w-full gap-5 sm:mt-12 sm:gap-8 ${layout}`}>
      {view.options.map((option, index) => (
        <Option key={`${view.name}:${option.label}`} option={option} index={index} big={big}
                on={index === view.highlight} onPick={() => app.click(index)} />
      ))}
    </div>
  );
}

function Option({option, index, big, on, onPick}) {
  const {Icon, tile} = visualFor(option.icon);
  return (
    <button
      type="button"
      data-index={index}
      tabIndex={-1}                                   // never focused: input.js handles keys
      aria-current={on ? 'true' : undefined}
      onMouseDown={event => event.preventDefault()}   // don't take focus
      onClick={onPick}
      className={`option relative flex flex-col items-center justify-center gap-4 rounded-[2rem] border-2 p-6 text-center transition-all duration-200 ease-out ${
        big ? 'min-h-56 sm:min-h-64' : 'min-h-44 sm:min-h-52'} ${
        on ? 'highlighted z-10 scale-[1.04] border-teal-600 bg-teal-600 text-white shadow-2xl shadow-teal-900/25 ring-8 ring-teal-300/50'
           : 'border-white bg-white/90 text-slate-700 shadow-sm hover:border-teal-200 hover:shadow-md'}`}
    >
      {on && (
        <span aria-hidden="true"
              className="absolute -top-4 left-1/2 -translate-x-1/2 rounded-full bg-amber-300 px-4 py-1 text-sm font-bold uppercase tracking-wider text-amber-950 shadow-md">
          Selected
        </span>
      )}
      <span aria-hidden="true"
            className={`icon grid place-items-center rounded-3xl transition-colors ${big ? 'size-24' : 'size-20'} ${on ? 'bg-white/20 text-white' : tile}`}>
        <Icon className={big ? 'size-12' : 'size-10'} strokeWidth={2} />
      </span>
      <span className="label text-2xl font-semibold leading-tight sm:text-3xl">{option.label}</span>
      {option.note && (
        <span className={`note rounded-full px-3 py-1 text-sm font-semibold ${on ? 'bg-white/20 text-white' : 'bg-teal-50 text-teal-800 ring-1 ring-teal-100'}`}>
          {option.note}
        </span>
      )}
    </button>
  );
}

function Footer({device}) {
  const tone = {
    ok: 'bg-emerald-50 text-emerald-800 ring-emerald-200',
    warn: 'bg-amber-50 text-amber-900 ring-amber-200',
    muted: 'bg-white/70 text-slate-500 ring-slate-200',
  }[device.tone];
  return (
    <div className="mt-auto flex flex-col items-center justify-between gap-3 pt-12 text-sm text-slate-500 sm:flex-row">
      <p className="hint flex flex-wrap items-center justify-center gap-1.5">
        <Keyboard className="mr-1 size-4" aria-hidden="true" />
        <Kbd>←</Kbd><Kbd>→</Kbd> move
        <span className="px-1 text-slate-300">·</span>
        <Kbd>Enter</Kbd> select
        <span className="px-1 text-slate-300">·</span>
        <Kbd>Esc</Kbd> back
      </p>
      <p id="device" className={`hint inline-flex items-center gap-2 rounded-full px-3 py-1.5 font-medium ring-1 empty:hidden ${tone}`}>
        {device.text && <><Gamepad2 className="size-4 shrink-0" aria-hidden="true" />{device.text}</>}
      </p>
    </div>
  );
}
