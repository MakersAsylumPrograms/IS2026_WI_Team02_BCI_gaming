import {HeartPulse} from 'lucide-react';

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

export function Disclaimer() {
  return (
    <footer className="px-4 py-6 text-center text-xs text-slate-400">
      Educational demonstration. Not a clinical alert system.
    </footer>
  );
}
