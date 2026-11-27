// Disposable HTTP/browser orchestration fixtures. Never connects to Platform or Docker.
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {createRequire} from 'node:module';
import assert from 'node:assert/strict';
const require=createRequire(path.resolve('../ringfour-platform/apps/console/package.json'));
const {chromium}=require('playwright');
const folder=await mkdtemp(path.join(tmpdir(),'rf-campaign-browser-'));
const child=spawn('python3',['-m','ringfour_acceptance','serve','--no-worker','--port','0','--state',folder],{stdio:['ignore','pipe','pipe']});
const url=await new Promise((resolve,reject)=>{let text='';const timer=setTimeout(()=>reject(new Error('Startup timeout')),10000);child.stdout.on('data',chunk=>{text+=chunk;const m=text.match(/http:\/\/127\.0\.0\.1:\d+/);if(m){clearTimeout(timer);resolve(m[0]);}});child.once('exit',code=>reject(new Error(`Exited ${code}`)));});
let browser;
const uid=n=>'00000000-0000-0000-0000-'+String(n).padStart(12,'0');
try {
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 const post=async(endpoint,data)=>{const r=await page.request.post(url+endpoint,{data});assert(r.ok(),await r.text());return r.json();};
 const workers={};
 for(const [i,name] of ['A','B'].entries()){
  const pairing=await post('/api/pair',{}),identity={user:{id:uid(i+1),email:`${name}@example.test`},organizations:[{id:uid(i+11),name:`Org ${name}`,robots:[{id:uid(i+21),name:`Robot ${name}`}]}]};
  const registration=await post('/worker/register',{pairing:pairing.code || pairing.command.match(/--pair (\w+)/)[1],label:name,identity,build:{compliance:true,fingerprint:'browser-fixture-build'}});
  workers[name]={identity,registration};
 }
 const workerPost=async(name,endpoint,data={})=>{
  const w=workers[name];const r=await fetch(url+endpoint,{method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${w.registration.token}`},body:JSON.stringify(data)});if(!r.ok)throw new Error(await r.text());return r.json();
 };
 await page.getByRole('button',{name:'AP tests',exact:true}).click();
 await page.locator('[data-role="A"] [data-role-worker]').selectOption('A');
 await page.locator('[data-role="B"] [data-role-worker]').selectOption('B');
 await page.locator('#environment-name').fill('Reusable browser fixture');
 await page.locator('#environment-api').fill('http://test');
 await page.getByRole('button',{name:'Save environment',exact:true}).click();
 await page.getByText('Saved',{exact:true}).waitFor();
 await page.locator('#campaign-scope').selectOption('preflight');
 await page.getByRole('button',{name:'Run campaign',exact:true}).click();
 await page.locator('.campaign-card').waitFor();
 for(const name of ['A','B']){
  const {job}=await workerPost(name,'/worker/poll',{identity:workers[name].identity});
  assert.equal(job.case_id,`preflight-${name}`);
  await workerPost(name,'/worker/update',{job_id:job.id,status:'passed',progress:{'case-report':{data:{baseline:{},robot_id:job.target.robot_id},checks:['Browser fixture identity checked']}},log:'Browser fixture only; no live Platform call.'});
 }
 await page.locator('.campaign-card>.section-heading').getByText('pending',{exact:true}).waitFor();
 assert.equal((await page.request.get(url+'/api/state').then(r=>r.json())).campaigns[0].full_ap_acceptance,false);
 await page.locator('#campaign-scope').selectOption('live');
 await page.getByRole('button',{name:'Run campaign',exact:true}).click();
 let campaign;
 for(let i=0;i<10;i++){
  const state=await page.request.get(url+'/api/state').then(r=>r.json());campaign=state.campaigns.find(c=>c.scope==='live');if(campaign)break;
  await new Promise(resolve=>setTimeout(resolve,100));
 }
 const first=(await workerPost('A','/worker/poll',{identity:workers.A.identity})).job;
 await workerPost('A','/worker/update',{job_id:first.id,progress:{'case-report':{data:{owned_installations:[uid(99)]}}}});
 await post('/api/campaigns/cancel',{campaign_id:campaign.id});
 await workerPost('A','/worker/update',{job_id:first.id,status:'interrupted',progress:{'case-report':{data:{owned_installations:[uid(99)]}}}});
 const cleanup=(await workerPost('A','/worker/poll',{identity:workers.A.identity})).job;
 assert.equal(cleanup.case_id,'cleanup-A');assert.deepEqual(cleanup.actors.A.data.owned_installations,[uid(99)]);
 await workerPost('A','/worker/update',{job_id:cleanup.id,status:'passed',progress:{'case-report':{data:{cleanup_confirmed:true}}}});
 const gate=page.locator('.campaign-card').first().locator('[data-case$=":operator-hosted-auth"]');
 await gate.locator('summary').click();
 await gate.locator('[name=operator]').fill('Fixture reviewer');
 // Polling must preserve an unfinished evidence form.
 await new Promise(resolve=>setTimeout(resolve,2300));
 assert.equal(await gate.locator('[name=operator]').inputValue(),'Fixture reviewer');
 await gate.locator('[name=reference]').fill('/tmp/browser-fixture-auth-trace');
 await gate.getByRole('button',{name:'Record evidence'}).click();
 await gate.getByText('manual pass',{exact:true}).waitFor();
 const report=await page.request.get(url+`/api/campaigns/${campaign.id}/report.md`);assert(report.ok());assert((await report.text()).includes('Hosted HTTPS/Auth0'));
 // Filling optional roles must include them without a separate checkbox step.
 await page.locator('#environment-editor').evaluate(el=>el.open=true);
 const cRole=page.locator('[data-role="C"]'),opRole=page.locator('[data-role="Operator"]');
 await cRole.locator('[data-role-worker]').selectOption('A');
 await cRole.locator('[data-role-field="robot_id"]').fill(uid(23));
 assert(await cRole.locator('[data-role-enabled]').isChecked());
 await opRole.locator('[data-role-worker]').selectOption('B');
 await opRole.locator('[data-role-field="org_id"]').fill(uid(11));
 await opRole.locator('[data-role-field="robot_id"]').fill('');
 assert(await opRole.locator('[data-role-enabled]').isChecked());
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/environments') && r.request().method()==='POST'),page.getByRole('button',{name:'Save environment',exact:true}).click()]);
 let saved=(await page.request.get(url+'/api/state').then(r=>r.json())).environments[0];
 assert.deepEqual(Object.keys(saved.roles),['A','B','C','Operator']);
 assert.equal(saved.roles.C.robot_id,uid(23));
 // An explicit opt-out stays excluded when saving without further edits.
 await cRole.locator('[data-role-enabled]').uncheck();
 await Promise.all([page.waitForResponse(r=>r.url().endsWith('/api/environments') && r.request().method()==='POST'),page.getByRole('button',{name:'Save environment',exact:true}).click()]);
 saved=(await page.request.get(url+'/api/state').then(r=>r.json())).environments[0];
 assert(!saved.roles.C);
 assert(saved.roles.Operator);
 await page.screenshot({path:'/tmp/ringfour-development-campaign.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/ringfour-development-campaign-mobile.png',fullPage:true});
 assert.deepEqual(errors,[]);
 console.log('Campaign browser: saved roles, worker queue, cancellation, owned cleanup, operator evidence, reports and mobile layout passed.');
} finally {await browser?.close();child.kill('SIGINT');await new Promise(resolve=>child.once('exit',resolve));await rm(folder,{recursive:true,force:true});}
