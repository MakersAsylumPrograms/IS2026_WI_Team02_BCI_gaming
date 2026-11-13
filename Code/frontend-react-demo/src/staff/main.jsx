import '@fontsource-variable/figtree';
import '../styles.css';
import {StrictMode} from 'react';
import {createRoot} from 'react-dom/client';
import {createStaffApp} from './controller.js';
import StaffApp from './StaffApp.jsx';

// The controller is created once, outside React, so StrictMode's double
// render/effect checks can never start polling or key listeners twice.
const app = createStaffApp();

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <StaffApp app={app} />
  </StrictMode>,
);
