// Optional UI integration test: requires Playwright and installed Microsoft Edge.
// Set PYTHON to the backend venv interpreter; provide playwright via NODE_PATH.
//
// Starts a temporary backend (temp database, test passwords), opens the patient
// and staff screens in separate browser contexts, and checks the whole loop:
// keyboard flow, confirm/cancel, Seen/Done, double SELECT, held keys, lost
// responses + refresh, offline banner, single device consumer, ESP32 commands
// (NEXT + SELECT only), two requests in a row, and no JavaScript errors.
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const path = require('node:path');
const assert = require('node:assert/strict');
const python = process.env.PYTHON || path.resolve(__dirname, '../.venv/Scripts/python.exe');
const code = `
import tempfile
from pathlib import Path
from app import create_app
from waitress import create_server
with tempfile.TemporaryDirectory() as temp:
 app=create_app(dict(DATABASE=str(Path(temp)/'browser.sqlite3'),PATIENT_PASSWORD='patient-test',STAFF_PASSWORD='staff-test',DEVICE_TOKEN='device-test'))
 server=create_server(app,host='127.0.0.1',port=0,threads=8)
 print('PORT='+str(server.effective_port),flush=True)
 server.run()
`;
const server = spawn(python, ['-u', '-c', code], {cwd:path.resolve(__dirname,'..'), stdio:['ignore','pipe','pipe']});
let browser;
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, label, ms = 8000) {
  const deadline = Date.now() + ms;
  while(Date.now() < deadline) { if(await fn()) return; await delay(100); }
  throw new Error('Timed out: ' + label);
}
const DEBOUNCE = 800;   // a little more than SELECT_DEBOUNCE_MS in input.js

(async () => {
  let output = '';
  server.stdout.on('data', data => output += data);
  server.stderr.on('data', data => process.stderr.write(data));
  await until(() => /PORT=(\d+)/.test(output), 'server startup');
  const base = 'http://127.0.0.1:' + output.match(/PORT=(\d+)/)[1];
  browser = await chromium.launch({channel:'msedge',headless:true});
  const patientContext = await browser.newContext();
  const staffContext = await browser.newContext();
  const patient = await patientContext.newPage(), staff = await staffContext.newPage();
  const errors=[];
  patient.on('pageerror', error=>errors.push(error.message));
  staff.on('pageerror', error=>errors.push(error.message));

  // ---- helpers ----
  const screen = () => patient.evaluate(() => document.body.dataset.screen);
  const title = () => patient.locator('#title').innerText();
  const highlighted = () => patient.locator('#options .option.highlighted .label').innerText();
  const press = async key => { await patient.keyboard.press(key); await delay(DEBOUNCE); };
  const rows = () => patient.evaluate(async()=> (await (await fetch('/api/v1/requests')).json()).requests);
  let seq = 0;
  const device = command => fetch(base+'/api/v1/input-events',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer device-test'},
    body:JSON.stringify({device_id:'esp32-01',boot_id:'browser-test',seq:++seq,source:'eog',command})});
  const deviceSays = async command => { assert.equal((await device(command)).status, 200); await delay(DEBOUNCE); };
  // Staff: press the Seen/Done button on the card for a request type label
  const staffButton = label => staff.locator('#active .request', {hasText: label}).locator('button.action');
  let posts = 0;
  patient.on('request', r => { if (r.method()==='POST' && r.url().endsWith('/api/v1/requests')) posts++; });

  for(const [page,role] of [[patient,'patient'],[staff,'staff']]) {
    await page.goto(base+'/'+role+'/');
    if (role === 'staff') await page.locator('#login-id').fill('staff');
    await page.locator('#password').fill(role+'-test');
    await page.locator('#password').press('Enter');   // sign in with the keyboard
    await page.locator('#app').waitFor({state:'visible'});
  }
  await until(async()=> await screen()==='menu', 'patient menu');

  // 1. Held arrow key moves the highlight only once.
  assert.equal(await highlighted(), 'Water');
  await patient.keyboard.down('ArrowRight'); await patient.keyboard.down('ArrowRight');  // 2nd = auto-repeat
  await patient.keyboard.up('ArrowRight');
  assert.equal(await highlighted(), 'Toilet assistance');
  await press('ArrowLeft');

  // 2. Confirm screen shows Confirm AND Cancel, starting on Cancel. Cancel sends nothing.
  await press('Enter');
  assert.equal(await screen(), 'confirm');
  assert.deepEqual(await patient.locator('#options .label').allInnerTexts(), ['Confirm','Cancel']);
  assert.equal(await highlighted(), 'Cancel');
  await press('Enter');
  assert.equal(await screen(), 'menu');
  assert.equal(posts, 0);

  // 3. Full loop. Double Enter on Confirm submits once.
  await press('Enter');                  // Water -> confirm
  await press('ArrowRight');             // Cancel -> Confirm
  assert.equal(await highlighted(), 'Confirm');
  await patient.keyboard.press('Enter'); await patient.keyboard.press('Enter');  // double press
  await until(async()=> await screen()==='status', 'status screen');
  assert.equal(await title(), 'Request sent ✓');
  await delay(DEBOUNCE);
  assert.equal(posts, 1);
  // Staff sees it with an alert, presses Seen -> patient sees "Help is coming"
  await staffButton('Water').waitFor();
  assert.equal(await staffButton('Water').innerText(), 'Seen');
  assert.ok(await staff.locator('#alert-banner').isVisible());
  await staffButton('Water').click();
  await until(async()=> await title()==='Help is coming ✓', 'patient ack');
  await until(async()=> await staffButton('Water').innerText()==='Done', 'staff done button');
  await staffButton('Water').click();
  await until(async()=> await title()==='Done ✓', 'patient resolved');
  await until(async()=> await screen()==='menu', 'return to menu', 9000);
  await until(async()=> (await staff.locator('#history-summary').innerText()).includes('(1)'), 'staff history');

  // 4. Response lost AFTER the server saved it -> "Not sure"; refresh recovers the real status.
  let dropped=false;
  await patient.route('**/api/v1/requests',async route=>{
    if(route.request().method()==='POST' && !dropped) {dropped=true; await route.fetch(); await route.abort();}
    else await route.continue();
  });
  await press('ArrowLeft');              // -> Call nurse (wraps around)
  assert.equal(await highlighted(), 'Call nurse');
  await press('Enter'); await press('ArrowRight'); await press('Enter');
  await until(async()=> await screen()==='uncertain', 'uncertain screen');
  await patient.reload();
  await until(async()=> await screen()==='status', 'recovered after reload');
  assert.equal((await rows()).filter(row=>row.type==='CALL_NURSE').length, 1);

  // 5. Request never reached the server -> "Not sure" -> Try again sends exactly one.
  await press('Escape');                 // status -> menu
  await patient.unroute('**/api/v1/requests');
  let blocked=false;
  await patient.route('**/api/v1/requests',async route=>{
    if(route.request().method()==='POST' && !blocked) {blocked=true; await route.abort();}
    else await route.continue();
  });
  await press('ArrowLeft');              // -> Adjust position
  await press('Enter'); await press('ArrowRight'); await press('Enter');
  await until(async()=> await screen()==='uncertain', 'uncertain (blocked)');
  assert.equal(await highlighted(), 'Try again');
  await press('Enter');
  await until(async()=> await screen()==='status', 'retry succeeded');
  assert.equal((await rows()).filter(row=>row.type==='REPOSITIONING').length, 1);
  await patient.unroute('**/api/v1/requests');

  // 6. Two requests at once on the staff screen: oldest pending first.
  await until(async()=> (await staff.locator('#active .request').count())===2, 'two staff cards');
  assert.match(await staff.locator('#active .request').first().innerText(), /Call nurse/);
  assert.match(await staff.locator('#summary').innerText(), /2 waiting/);

  // 7. Offline banner and recovery.
  await patientContext.setOffline(true);
  await until(()=> patient.locator('#offline-banner').isVisible(), 'offline banner');
  await patientContext.setOffline(false);
  await until(async()=> (await patient.locator('#connection').innerText()).includes('Connected'), 'reconnection');

  // 8. A second patient tab must not take over the ESP32 controls.
  await delay(350);
  const second=await patientContext.newPage();
  await second.goto(base+'/patient/');
  await until(async()=> (await second.locator('#device').innerText()).includes('Another patient screen'), 'consumer exclusion', 10000);
  await second.close();

  // 9. ESP32 with only NEXT + SELECT: cancel once, then send, then back to menu.
  await patient.bringToFront();
  await press('Escape');
  await until(async()=> await screen()==='menu', 'menu before device test');
  await until(async()=> (await patient.locator('#device').innerText()).includes('ESP32 controller'), 'device input ready', 10000);
  while (await highlighted() !== 'Water') await deviceSays('NEXT');
  await deviceSays('SELECT');            // Water -> confirm (on Cancel)
  await until(async()=> await screen()==='confirm', 'device: confirm');
  await deviceSays('SELECT');            // Cancel
  await until(async()=> await screen()==='menu', 'device: cancelled');
  await deviceSays('SELECT');            // Water -> confirm
  await deviceSays('NEXT');              // -> Confirm
  await deviceSays('SELECT');            // send
  await until(async()=> await screen()==='status', 'device: sent');
  await deviceSays('SELECT');            // "Back to menu"
  await until(async()=> await screen()==='menu', 'device: back to menu');
  assert.equal((await rows()).filter(row=>row.type==='WATER' && row.status==='PENDING').length, 1);

  // 10. Staff screen in the SAME browser as the patient (shared cookie): both keep working.
  const sharedStaff = await patientContext.newPage();
  sharedStaff.on('pageerror', error=>errors.push(error.message));
  await sharedStaff.goto(base+'/staff/');
  await sharedStaff.locator('#login-id').fill('staff');
  await sharedStaff.locator('#password').fill('staff-test');
  await sharedStaff.locator('#password').press('Enter');
  await sharedStaff.locator('#app').waitFor({state:'visible'});
  const sharedWater = sharedStaff.locator('#active .request', {hasText: 'Water'}).locator('button.action');
  await sharedWater.waitFor();
  await sharedWater.click();             // Seen, from the shared-browser staff tab
  const waterNote = patient.locator('#options .option', {hasText: 'Water'}).locator('.note');
  await until(async()=> (await waterNote.count()) && (await waterNote.innerText())==='Help is coming ✓', 'shared-browser ack');
  assert.equal(await patient.locator('#app').isVisible(), true);     // patient was not signed out
  assert.equal(await sharedStaff.locator('#app').isVisible(), true);
  await sharedStaff.close();
  await patient.bringToFront();

  assert.deepEqual(errors,[]);
  // Both dashboards restore their signed cookie after a normal reload.
  for (const page of [patient, staff]) {
    await page.reload();
    await page.locator('#app').waitFor({state:'visible'});
    assert.equal(await page.locator('#login').isVisible(), false);
  }
  const storedCookies = await patientContext.cookies();
  assert.ok(storedCookies.some(cookie => cookie.name === 'session' && cookie.httpOnly));
  // A failed logout must leave the app signed in and show an honest error.
  await patient.route('**/api/v1/session', async route => {
    if (route.request().method() === 'DELETE') await route.abort();
    else await route.continue();
  });
  await patient.locator('#logout').click();
  await until(async()=> (await patient.locator('p[role="status"]').allTextContents()).some(t=>t.includes('Could not confirm sign out')), 'logout error');
  assert.equal(await patient.locator('#app').isVisible(), true);
  await patient.unroute('**/api/v1/session');
  await patient.locator('#logout').click();
  await patient.locator('#login').waitFor({state:'visible'});
  await patient.reload();
  await patient.locator('#login').waitFor({state:'visible'});
  if(process.env.EQUALCARE_SCREENSHOT_DIR) {
    await patient.screenshot({path:path.join(process.env.EQUALCARE_SCREENSHOT_DIR,'equalcare-patient.png'),fullPage:true});
    await staff.screenshot({path:path.join(process.env.EQUALCARE_SCREENSHOT_DIR,'equalcare-staff.png'),fullPage:true});
  }
  console.log('PASS: held key, confirm/cancel, full loop, double SELECT, lost response + refresh, retry, two requests, offline, sole consumer, NEXT+SELECT device flow, shared-browser tabs, no JS errors');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  server.kill();
});
