import '@fontsource-variable/figtree';
import '../styles.css';
import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {createPatientApp} from './controller.js';
import PatientApp from './PatientApp.jsx';

// The controller is created once, outside React, so StrictMode's double
// render/effect checks can never start polling or key listeners twice.
const app = createPatientApp();

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <PatientApp app={app} />
  </StrictMode>,
);
