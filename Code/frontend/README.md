# EqualCare screens (React + Tailwind)

The patient bedside screen, the nurse dashboard and the landing page. They talk to the
backend in `../backend` through `src/lib/api.js` (`/api/v1/...`, same origin).

The backend serves the **built** files in `dist/` at `/`, `/patient/` and `/staff/`.
`Start-EqualCare.cmd` builds them when `dist/` is missing or older than the source
(Node.js 20 or newer; the first build needs internet). By hand:

```powershell
cd Code/frontend
npm install      # first time only
npm run build    # writes dist/
```

Working on the design with live reload (the backend must be running on port 8000):

```powershell
npm run dev      # http://localhost:5173 · /patient/ · /staff/
```

| Path | What it is |
| --- | --- |
| `index.html`, `patient/index.html`, `staff/index.html` | Empty page shells Vite builds from |
| `src/lib/api.js` | HTTP client, request types and labels, server-clock offset |
| `src/lib/input.js` | Keyboard, mouse and device commands; `fromDevice()` is where eye and joystick commands arrive |
| `src/lib/session.js`, `store.js` | Sign-in, connection indicator, polling, the store the views redraw from |
| `src/patient/controller.js`, `PatientApp.jsx` | Patient logic (menu, confirm, send, retry, status) and its view |
| `src/staff/controller.js`, `StaffApp.jsx` | Nurse logic (ordering, Seen/Done, timers, sound) and its view |
| `src/components/`, `src/styles.css` | Shared page frame, small UI pieces and the design tokens |

The backend's content-security policy is `default-src 'self'`: no inline scripts or
`style` attributes, and fonts and images must be real files (the Vite config already
does that). `dist/` and `node_modules/` are not committed.
