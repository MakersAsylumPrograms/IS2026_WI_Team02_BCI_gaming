# EqualCare — React + Tailwind design demo

A **standalone preview** of the redesigned patient screen and nurse dashboard.
It is **not connected** to the backend, the ESP32 or the acquisition code:
`src/lib/demoApi.js` fakes the API inside the browser (data in localStorage,
any password works). The real app is still the plain HTML in `../frontend`.

```powershell
cd Code/frontend-react-demo
npm install      # first time only (Node.js 20+)
npm run dev      # http://localhost:5173  (landing) · /patient/ · /staff/
```

Open the patient and staff pages in two tabs side by side: a request sent on the
patient screen shows up on the staff dashboard. "Reset demo" in the top ribbon
clears everything.

Integrating later: in `src/patient/controller.js` and `src/staff/controller.js`
replace `new DemoAPI(...)` with `new EqualCareAPI(...)` from `src/lib/api.js`.
