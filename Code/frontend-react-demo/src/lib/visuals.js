import {ArrowLeft, BedSingle, BellRing, Check, CircleHelp, GlassWater, RotateCw, Toilet, X} from 'lucide-react';

// One picture + colour per request type / patient action, shared by the
// patient and staff screens so "Water" always looks the same everywhere.
// (Class names are written out in full so Tailwind can find them.)
const VISUALS = {
  WATER:             {Icon: GlassWater, tile: 'bg-sky-100 text-sky-700'},
  TOILET_ASSISTANCE: {Icon: Toilet,     tile: 'bg-violet-100 text-violet-700'},
  REPOSITIONING:     {Icon: BedSingle,  tile: 'bg-amber-100 text-amber-700'},
  CALL_NURSE:        {Icon: BellRing,   tile: 'bg-rose-100 text-rose-700'},
  CONFIRM:           {Icon: Check,      tile: 'bg-emerald-100 text-emerald-700'},
  CANCEL:            {Icon: X,          tile: 'bg-slate-100 text-slate-600'},
  RETRY:             {Icon: RotateCw,   tile: 'bg-teal-100 text-teal-700'},
  BACK:              {Icon: ArrowLeft,  tile: 'bg-slate-100 text-slate-600'},
};

export function visualFor(key) {
  return VISUALS[key] || {Icon: CircleHelp, tile: 'bg-slate-100 text-slate-600'};
}

export function greeting(date = new Date()) {
  const hour = date.getHours();
  return hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
}
