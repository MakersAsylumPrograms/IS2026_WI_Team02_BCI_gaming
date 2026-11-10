// Optional UI integration test: requires Playwright and installed Microsoft Edge.
// Set PYTHON to the backend venv interpreter; provide playwright via NODE_PATH.
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
async function until(fn, label) {
  const deadline = Date.now() + 8000;
  while(Date.now() < deadline) { if(await fn()) return; await delay(100); }
  throw new Error('Timed out: ' + label);
}
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
  for(const [page,role] of [[patient,'patient'],[staff,'staff']]) {
    await page.goto(base+'/'+role+'/');
    await page.locator('#password').fill(role+'-test');
    await page.locator('#login button').click();
    await page.locator('#dashboard').waitFor({state:'visible'});
  }
  await patient.getByRole('button',{name:'Water',exact:true}).click();
  await staff.getByRole('button',{name:'Acknowledge',exact:true}).waitFor();
  await staff.getByRole('button',{name:'Acknowledge',exact:true}).click();
  await until(async()=> (await patient.locator('#active').innerText()).includes('Acknowledged by nurse'),'patient ack');
  await staff.getByRole('button',{name:'Resolve',exact:true}).click();
  await until(async()=> (await patient.locator('#active').innerText()).includes('No active requests'),'resolution');
  // Drop response AFTER server commit; refreshing and retrying must not duplicate.
  let dropped=false;
  await patient.route('**/api/v1/requests',async route=>{
    if(route.request().method()==='POST' && !dropped) {dropped=true; await route.fetch(); await route.abort();}
    else await route.continue();
  });
  await patient.getByRole('button',{name:'Call nurse',exact:true}).click();
  await patient.locator('#retry').waitFor({state:'visible'});
  await patient.reload();
  await patient.locator('#retry').waitFor({state:'visible'});
  await patient.locator('#retry').click();
  await patient.locator('#retry').waitFor({state:'hidden'});
  const rows=await patient.evaluate(async()=> (await (await fetch('/api/v1/requests')).json()).requests);
  assert.equal(rows.filter(row=>row.type==='CALL_NURSE').length,1);
  // Offline UI preserves requests and recovers without a fresh selection.
  await patientContext.setOffline(true);
  await until(async()=> (await patient.locator('#connection').innerText()).includes('unavailable'),'offline indication');
  await patientContext.setOffline(false);
  await until(async()=> (await patient.locator('#connection').innerText()).includes('connected'),'reconnection');
  // Fresh tab shares login but must not share the input-consumer lease.
  await delay(350);
  const second=await patientContext.newPage();
  await second.goto(base+'/patient/');
  await until(async()=> (await second.locator('#input-status').innerText()).includes('Another patient tab'),'consumer exclusion');
  await second.close();
  // Feed real device JSON and check patient menu changes without a request.
  await patient.bringToFront();
  await until(async()=> (await patient.locator('#input-status').innerText()).includes('ready'),'input ready');
  const before=await patient.locator('#menu .selected').innerText();
  const sent=await fetch(base+'/api/v1/input-events',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer device-test'},body:JSON.stringify({device_id:'esp32-01',boot_id:'browser-test',seq:1,source:'joystick',command:'NEXT'})});
  assert.equal(sent.status,200);
  await until(async()=> (await patient.locator('#menu .selected').innerText())!==before,'device navigation');
  assert.deepEqual(errors,[]);
  if(process.env.EQUALCARE_SCREENSHOT_DIR) {
    await patient.screenshot({path:path.join(process.env.EQUALCARE_SCREENSHOT_DIR,'equalcare-patient.png'),fullPage:true});
    await staff.screenshot({path:path.join(process.env.EQUALCARE_SCREENSHOT_DIR,'equalcare-staff.png'),fullPage:true});
  }
  console.log('PASS: browser request/ack/resolve, uncertain delivery + refresh + retry, offline recovery, sole consumer, device navigation, no JS errors');
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(async()=>{
  if(browser) await browser.close();
  server.kill();
});
