import {FlaskConical, HeartPulse, RotateCcw} from 'lucide-react';
import {resetDemo} from '../lib/demoApi.js';

// Logo + name. Pass href to make it a link (not on the patient screen, so a
// stray click can never navigate the patient away).
export function Brand({subtitle, href}) {
  const Tag = href ? 'a' : 'div';
  return (
    <Tag href={href} className="group flex items-center gap-3 rounded-xl focus-visible:outline-offset-4">
      <span className="grid size-10 place-items-center rounded-xl bg-linear-to-br from-teal-500 to-emerald-500 text-white shadow-md shadow-teal-700/20 transition-transform group-hover:scale-105">
        <HeartPulse className="size-5" strokeWidth={2.25} aria-hidden="true" />
      </span>
      <span className="leading-tight">
        <span className="block text-lg font-bold tracking-tight text-slate-900">EqualCare</span>
        {subtitle && <span className="block text-xs font-medium text-slate-500">{subtitle}</span>}
      </span>
    </Tag>
  );
}

export function Card({className = '', pad = 'p-5', children, ...rest}) {
  return (
    <div className={`rounded-2xl border border-slate-200/70 bg-white/90 shadow-sm shadow-slate-900/[0.03] ${pad} ${className}`} {...rest}>
      {children}
    </div>
  );
}

export function Kbd({children}) {
  return (
    <kbd className="inline-flex min-w-7 items-center justify-center rounded-md border border-slate-300 border-b-2 bg-white px-1.5 py-0.5 font-sans text-xs font-semibold text-slate-700">
      {children}
    </kbd>
  );
}

// Thin ribbon on every page: this is a design preview, not the live system.
export function DemoBanner() {
  return (
    <div className="bg-teal-900 text-teal-50">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-x-3 gap-y-1 px-4 py-2 text-center text-xs font-medium sm:text-sm">
        <FlaskConical className="size-4 shrink-0 text-teal-300" aria-hidden="true" />
        <span>Demo preview — not connected to EqualCare. Requests only live in this browser; any password works.</span>
        <button type="button"
                onClick={() => { resetDemo(); window.location.reload(); }}
                className="inline-flex items-center gap-1 rounded-full bg-white/10 px-2.5 py-0.5 font-semibold text-white transition hover:bg-white/20">
          <RotateCcw className="size-3.5" aria-hidden="true" />
          Reset demo
        </button>
      </div>
    </div>
  );
}

export function Disclaimer() {
  return (
    <footer className="px-4 py-6 text-center text-xs text-slate-400">
      Educational demonstration. Not a clinical alert system.
    </footer>
  );
}
