import {ArrowRight, BedSingle, Eye, Gamepad2, ShieldCheck, Stethoscope} from 'lucide-react';
import {Brand, DemoBanner, Disclaimer} from '../components/ui.jsx';

const ROLES = [
  {
    href: '/patient/',
    Icon: BedSingle,
    title: 'Patient screen',
    text: 'Large, calm choices for the bedside. Navigate with eye movements, a joystick or the keyboard.',
    tile: 'bg-teal-50 text-teal-700 ring-teal-100',
    hover: 'hover:border-teal-300',
    cta: 'text-teal-700',
  },
  {
    href: '/staff/',
    Icon: Stethoscope,
    title: 'Staff dashboard',
    text: 'A live request queue with gentle alerts, waiting timers and one-tap Seen / Done.',
    tile: 'bg-emerald-50 text-emerald-700 ring-emerald-100',
    hover: 'hover:border-emerald-300',
    cta: 'text-emerald-700',
  },
];

const FEATURES = [
  {Icon: Eye, text: 'Eye-movement control'},
  {Icon: Gamepad2, text: 'Joystick & keyboard'},
  {Icon: ShieldCheck, text: 'Never sends a request twice'},
];

export default function Landing() {
  return (
    <div className="flex min-h-screen flex-col">
      <DemoBanner />
      <header className="mx-auto flex w-full max-w-6xl items-center px-4 py-5 sm:px-6">
        <Brand subtitle="Assistive nurse call" />
        <span className="ml-auto rounded-full bg-white/70 px-3 py-1 text-xs font-semibold text-slate-500 ring-1 ring-slate-200">
          Design preview
        </span>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 pb-16 pt-10 sm:px-6 sm:pt-16">
        <div className="max-w-3xl motion-safe:animate-rise">
          <h1 className="text-balance text-4xl font-bold leading-[1.1] tracking-tight text-slate-900 sm:text-6xl">
            Care that{' '}
            <span className="bg-linear-to-r from-teal-600 to-emerald-500 bg-clip-text text-transparent">listens</span>
            , even without words.
          </h1>
          <p className="mt-5 max-w-2xl text-pretty text-lg text-slate-600 sm:text-xl">
            EqualCare lets patients with limited movement ask for help using small eye movements,
            and lets nurses see and answer every request in real time.
          </p>
          <ul className="mt-6 flex flex-wrap gap-2">
            {FEATURES.map(({Icon, text}) => (
              <li key={text} className="inline-flex items-center gap-2 rounded-full bg-white/80 px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm ring-1 ring-slate-200/70">
                <Icon className="size-4 text-teal-600" aria-hidden="true" />
                {text}
              </li>
            ))}
          </ul>
        </div>

        <h2 className="mt-14 text-sm font-semibold uppercase tracking-wider text-slate-400">Which screen is this laptop?</h2>
        <nav className="mt-4 grid gap-5 sm:grid-cols-2">
          {ROLES.map(role => (
            <a key={role.href} href={role.href}
               className={`card group flex flex-col rounded-3xl border-2 border-white bg-white/90 p-7 shadow-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-teal-900/5 ${role.hover}`}>
              <span className={`grid size-14 place-items-center rounded-2xl ring-1 ${role.tile}`}>
                <role.Icon className="size-7" aria-hidden="true" />
              </span>
              <span className="mt-5 text-2xl font-bold tracking-tight text-slate-900">{role.title}</span>
              <span className="mt-2 text-slate-500">{role.text}</span>
              <span className={`mt-6 inline-flex items-center gap-1.5 font-semibold ${role.cta}`}>
                Open
                <ArrowRight className="size-4 transition-transform group-hover:translate-x-1" aria-hidden="true" />
              </span>
            </a>
          ))}
        </nav>
        <p className="mt-6 text-sm text-slate-400">
          Tip: open both screens in two tabs side by side. A request sent on the patient screen appears on the staff dashboard.
        </p>
      </main>

      <Disclaimer />
    </div>
  );
}
