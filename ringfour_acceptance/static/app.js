'use strict';
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state = {workers: [], jobs: [], setup: {targets: [], repository: ''}}, draft = [], setupInitialized = false, dirty = false;
const profileNames = {quick:'Quick checks', integration:'Integration checks', full:'Full automated sweep', preflight:'Live preflight', live:'Live App workflow', virtual:'Virtual robot launcher'};
const descriptions = {quick:'Contracts, Console types and interactions, CLI and Daemon tests.', integration:'Real protocol checks with disposable services. Requires prepared dependencies and Docker.', full:'All component, protocol and fixture browser suites. Live robot workflows remain separate.', preflight:'Reads identity, ownership and robot readiness. No Apps are installed or started.', live:'Publishes a private Robot Check App, installs it, reads IMU samples, changes test settings and exercises lifecycle/cleanup.'};
async function api(path, body) {
  const request = body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  let response = await fetch(path, request);
  // A control-room restart rotates only this local UI cookie. Bootstrap it
  // through the same-origin home page, then retry the rejected request once.
  if(response.status===403 && path.startsWith('/api/')) {
    const bootstrap=await fetch('/',{cache:'no-store'});
    if(bootstrap.ok) response=await fetch(path,request);
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function failure(error) { $('#error').textContent = error.message; $('#error').hidden = false; }
function clearError() { $('#error').hidden = true; }
function page(name) {
  for (const tab of document.querySelectorAll('.tab')) tab.classList.toggle('active', tab.dataset.page === name);
  $('#runs-page').hidden = name !== 'runs'; $('#setup-page').hidden = name !== 'setup'; $('#virtual-page').hidden = name !== 'virtual'; $('#campaigns-page').hidden = name !== 'campaigns';
}
for (const tab of document.querySelectorAll('.tab')) tab.onclick = () => page(tab.dataset.page);
function options(items, selected, placeholder='Select…') {
  return `<option value="">${esc(placeholder)}</option>` + items.map(item => `<option value="${esc(item.id)}" ${item.id === selected ? 'selected' : ''}>${esc(item.label || item.name || item.id)}</option>`).join('');
}
function workerName(worker) {
  const user = worker.identity.user;
  return `${worker.label}${user ? ' · ' + (user.email || user.display_name || user.id) : ' · login needed'}`;
}
function renderWorkers() {
  $('#worker-count').textContent = state.workers.filter(w => w.online).length;
  $('#workers').innerHTML = state.workers.length ? state.workers.map(w => `<div class="worker"><div class="worker-heading"><h3>${esc(workerName(w))}</h3><span class="badge ${w.online ? 'passed' : ''}">${w.online ? 'Connected' : 'Offline'}</span></div><p class="run-meta">${esc(w.identity.user?.id || 'No authenticated user')}</p><p>${esc(w.identity.error || w.identity.api_url || '')}</p></div>`).join('') : '<p class="muted">Waiting for a worker. Connect an account to begin.</p>';
  const value = $('#runner').value;
  $('#runner').innerHTML = options(state.workers.filter(w => w.online).map(w => ({id:w.id,label:workerName(w)})), value, 'Choose a connected account');
  if (!$('#runner').value && state.workers.some(w => w.online)) $('#runner').value = state.workers.find(w => w.online).id;
}
function renderTargets() {
  $('#targets').innerHTML = draft.length ? draft.map((target, index) => {
    const worker = state.workers.find(w => w.id === target.worker_id);
    const orgs = worker?.identity.organizations || [];
    const org = orgs.find(o => o.id === target.org_id);
    return `<div class="target" data-index="${index}"><div class="target-fields"><label>User<select data-field="worker_id">${options(state.workers.filter(w => w.identity.user).map(w => ({id:w.id,label:workerName(w)})),target.worker_id)}</select></label><label>Organization<select data-field="org_id">${options(orgs.map(o => ({...o,label:`${o.name} · ${o.id}`})),target.org_id)}</select></label><label>Robot<select data-field="robot_id">${options((org?.robots || []).map(r => ({...r,label:`${r.name} · ${r.id}`})),target.robot_id)}</select></label></div><div class="target-bottom"><span class="run-meta">${esc(target.user_id || 'Choose a connected user')}</span><button type="button" data-remove="${index}">Remove</button></div>${worker && target.org_id && worker.identity.selected_org !== target.org_id ? '<p class="notice">Select this organization with ringfour-cli org select in this worker’s shell. Discovery refreshes every 30 seconds.</p>' : ''}</div>`;
  }).join('') : '<p class="muted">No live targets selected. Add one for each account you want to test.</p>';
  for (const select of $('#targets').querySelectorAll('select')) select.onchange = () => {
    const index = Number(select.closest('.target').dataset.index), field = select.dataset.field;
    draft[index][field] = select.value;
    if (field === 'worker_id') {
      draft[index].user_id = state.workers.find(w => w.id === select.value)?.identity.user?.id || '';
      draft[index].org_id = ''; draft[index].robot_id = '';
    }
    if (field === 'org_id') draft[index].robot_id = '';
    dirty = true; $('#saved').textContent = ''; renderTargets();
  };
  for (const button of $('#targets').querySelectorAll('[data-remove]')) button.onclick = () => { draft.splice(Number(button.dataset.remove),1); dirty=true; renderTargets(); };
}
$('#add-target').onclick = () => { draft.push({worker_id:'',user_id:'',org_id:'',robot_id:''}); dirty=true; renderTargets(); };
$('#repository').oninput = () => {dirty=true; $('#saved').textContent='';};
$('#setup-form').onsubmit = async event => {
  event.preventDefault(); clearError();
  try {
    state.setup = await api('/api/setup',{targets:draft,repository:$('#repository').value});
    dirty=false; $('#saved').textContent='Setup saved'; profileChanged();
  } catch(error) { failure(error); }
};
$('#pair').onclick = async () => {
  clearError();
  try {
    const pairing = await api('/api/pair',{});
    $('#pair-command').textContent = pairing.command;
    $('#copy-pair').textContent='Copy command';
    $('#pairing').hidden=false;
  } catch(error) { failure(error); }
};
$('#copy-pair').onclick = async () => {
  try {
    const pairing = await api('/api/pair',{});
    $('#pair-command').textContent = pairing.command;
    await navigator.clipboard.writeText(pairing.command);
    $('#copy-pair').textContent='Copied fresh command';
  }
  catch { failure(new Error('Select and copy the command above.')); }
};
function profileChanged() {
  const profile = $('#profile').value, live = ['preflight','live'].includes(profile);
  $('#profile-help').textContent=descriptions[profile];
  $('#runner-field').hidden=live; $('#target-summary').hidden=!live;
  $('#target-summary').textContent=state.setup.targets.length ? `${state.setup.targets.length} saved target${state.setup.targets.length > 1 ? 's' : ''}. Each runs under its own connected account.` : 'Choose live targets in Setup first.';
  $('#start-run').disabled = live ? !state.setup.targets.length : !state.workers.some(w => w.online);
}
$('#profile').onchange=profileChanged;
$('#run-form').onsubmit = async event => {
  event.preventDefault(); clearError(); $('#start-run').disabled=true;
  try { await api('/api/runs',{profile:$('#profile').value,worker_id:$('#runner').value}); await refresh(); }
  catch(error) {failure(error);}
  finally {profileChanged();}
};
function renderRuns() {
  $('#run-count').textContent=state.jobs.length;
  $('#history-count').textContent=`${state.jobs.length} run${state.jobs.length === 1 ? '' : 's'}`;
  if (!state.jobs.length) {
    $('#history').innerHTML='<div class="empty"><h2>Ready when you are.</h2><p>Connect your accounts in Setup, or run the quick checks with the current worker.</p><button id="empty-setup">Open setup</button></div>';
    $('#empty-setup').onclick=()=>page('setup'); return;
  }
  // Retain open logs across polling updates.
  const opened=new Set([...$('#history').querySelectorAll('details[open]')].map(d=>d.dataset.job));
  $('#history').innerHTML=state.jobs.map(job=>{
    const report=job.progress.report || {}, workflow=job.progress['live-workflow'] || {}, operation=job.progress['virtual-operation'];
    const suites=report.suites || [], selected=report.selected || [];
    const isTerminal=['passed','failed','pending','interrupted'].includes(job.status);
    const runningMatch=job.log.match(/Running ([\w-]+) \([^\n]+/g);
    const current=runningMatch?.at(-1)?.replace(/^Running /,'') || (job.status==='queued' ? 'Waiting for worker' : 'Preparing checks');
    const completed=suites.filter(s=>s.status !== 'running').length;
    const percent=isTerminal ? 100 : (selected.length ? Math.round(completed/selected.length*100) : 0);
    const active=suites.find(s=>s.status==='running');
    const elapsed=s=>s.seconds ?? (s.started_at ? Math.max(0,(Date.now()-Date.parse(s.started_at))/1000) : null);
    const timing=s=>elapsed(s) === null ? '' : `${elapsed(s).toFixed(1)}s`;
    const suiteRows=suites.map(s=>{
      const content=job.files[s.log || `${s.id}.log`];
      const detailsId=`${job.id}:${s.id}`;
      return `<div class="suite-block"><div class="suite-row"><span>${esc(s.title || s.id)}</span><span class="suite-result"><span class="muted">${esc(timing(s))}${s.returncode != null ? ' · exit '+esc(s.returncode) : ''}</span><span class="badge ${esc(s.status)}">${esc(s.status)}</span></span></div>${s.reason ? `<p class="help">${esc(s.reason)}</p>` : ''}<details data-job="${esc(detailsId)}" ${opened.has(detailsId) ? 'open' : ''}><summary>${esc(s.id)} output</summary><pre>${esc(content || 'Waiting for test output…')}</pre></details></div>`;
    }).join('');
    const log=job.log || job.files['runner.log'] || '';
    return `<article class="run-card" data-job="${esc(job.id)}"><div class="run-head"><div><h3>${esc(job.profile === 'virtual' ? `${job.launcher.spec.action} · ${job.stack_name || job.launcher.projects.join(', ')}` : job.case_title || profileNames[job.profile])}</h3><p>${esc(job.worker_label)} · ${esc(new Date(job.created_at).toLocaleString())}</p></div><span class="badge ${esc(job.status)}">${esc(job.status)}</span></div><div class="run-body"><div class="progress" role="progressbar" aria-label="Completed suites" aria-valuenow="${percent}" aria-valuemin="0" aria-valuemax="100"><div data-progress="${percent}"></div></div>${!isTerminal ? `<p class="help">${esc(job.status==='stopping' ? 'Cancellation requested. Waiting for runner cleanup…' : operation?.stage || workflow.stage || (active ? `${active.title || active.id} · ${timing(active)} elapsed` : current))}</p>` : ''}${suiteRows}${operation ? `<p class="help">${esc(operation.error || (operation.passed ? 'Launcher completed' : operation.stage))}</p><p class="run-meta">${esc(job.launcher.command)}</p>` : ''}${(workflow.checks||[]).map(check=>`<div class="suite-row"><span>${esc(check)}</span><span class="badge passed">passed</span></div>`).join('')}${workflow.cleanup?.length ? `<p class="help">Cleanup: ${esc(workflow.cleanup.map(c=>c.error ? 'needs attention: '+c.error : 'confirmed').join(' · '))}</p>` : ''}${job.reason ? `<p class="help">${esc(job.reason)}</p>` : ''}${job.target ? `<p class="run-meta">User ${esc(job.target.user_id)}<br>Organization ${esc(job.target.org_id)}<br>Robot ${esc(job.target.robot_id)}</p>` : ''}<div class="actions"><a href="/api/runs/${job.id}/report.json" download="${job.id}.json">Download run</a>${Object.keys(job.files).filter(name=>name.endsWith('.json') || name.endsWith('.md')).map(name=>`<a href="/api/runs/${job.id}/files/${encodeURIComponent(name)}" target="_blank" rel="noopener">${esc(name)}</a>`).join('')}${!isTerminal ? `<button data-cancel="${job.id}">Cancel run</button>` : ''}</div><details data-job="${job.id}" ${opened.has(job.id) ? 'open' : ''}><summary>Logs</summary><pre>${esc(log || job.log || 'Waiting for output…')}</pre></details></div></article>`;
  }).join('');
  for(const bar of $('#history').querySelectorAll('[data-progress]')) bar.style.width=`${bar.dataset.progress}%`;
  for(const button of $('#history').querySelectorAll('[data-cancel]')) button.onclick=async()=>{try{const job=state.jobs.find(j=>j.id===button.dataset.cancel);await api(job?.campaign_id?'/api/campaigns/cancel':'/api/cancel',job?.campaign_id?{campaign_id:job.campaign_id}:{job_id:button.dataset.cancel});await refresh();}catch(error){failure(error);}};
}
async function refresh() {
  try {
    state=await api('/api/state');
    $('#connection').textContent=`${state.workers.filter(w=>w.online).length} worker${state.workers.filter(w=>w.online).length === 1 ? '' : 's'} connected`;
    if(!setupInitialized){draft=structuredClone(state.setup.targets);$('#repository').value=state.setup.repository;setupInitialized=true;renderTargets();}
    const workerSignature=JSON.stringify(state.workers.map(w=>[w.id,w.online,w.identity,w.launchers]));
    if(refresh.signature!==workerSignature){renderWorkers();renderTargets();refresh.signature=workerSignature;}
    renderRuns();renderStacks();profileChanged();if(typeof renderCampaigns === 'function')renderCampaigns();
  } catch(error) {$('#connection').textContent='Control room disconnected';}
}
refresh();setInterval(refresh,2000);


const openedStackOutput = new Set();
const selectedContainerLogs = new Map();
function stackTitle(stack) { return stack.topology==='singleton' ? 'Existing stack' : stack.name; }
function stackWorker(stack) { return state.workers.find(w=>w.id===stack.worker_id); }
function stackJob(stack) {
  return state.jobs.find(j=>j.stack_id===stack.id || (j.profile==='virtual' && j.launcher?.root===stack.root && j.launcher.spec.topology===stack.topology && stack.index>=j.launcher.spec.start_index && stack.index<j.launcher.spec.start_index+j.launcher.spec.count));
}
function renderStacks() {
  const capable=state.workers.filter(w=>w.online && w.launchers?.available);
  $('#add-stack').disabled=!capable.length;
  const settingsOpen=new Set([...$('#stack-cards').querySelectorAll('details.stack-settings[open]')].map(d=>d.dataset.stack));
  const outputOpen=new Set([...$('#stack-cards').querySelectorAll('details.stack-output[open]')].map(d=>d.dataset.stack));
  const stacks=state.stacks || [];
  if(!stacks.length){$('#stack-cards').innerHTML='<div class="empty"><h2>No stacks yet.</h2><p>Existing stacks appear when a launcher worker connects. Add a stack to prepare another robot.</p></div>';return;}
  $('#stack-cards').innerHTML=stacks.map(stack=>{
    const worker=stackWorker(stack), latest=stackJob(stack);
    const busy=latest && !['passed','failed','pending','interrupted'].includes(latest.status);
    const online=worker?.online && worker.launchers?.available;
    const unavailable=!online || !!worker?.launchers?.inventory_error;
    const components=stack.components || {};
    const ports=stack.ports || {};
    const identityLabel=stack.robot_id && (stack.identity_state!=='enrolled' || unavailable) ? 'Last known robot ID' : 'Robot ID';
    const identityText=stack.robot_id || (stack.identity_state==='not_enrolled' ? 'Not enrolled' : 'Unavailable');
    const running=Object.values(components).filter(c=>c.state==='running').length;
    const managed=Object.values(components).filter(c=>c.state!=='external');
    const allRunning=managed.length>0 && managed.every(c=>c.state==='running');
    const activity=latest?.status==='queued' ? 'Queued' : ({build:'Building images',start:'Starting',stop:'Stopping',delete:'Deleting',daemon:'Restarting daemon',status:'Checking',logs:'Reading logs'}[latest?.launcher?.spec.action] || 'Working');
    const overall=busy ? activity : unavailable ? 'Unavailable' : running===3 ? 'Running' : running ? `${running} running` : 'Not started';
    const actionBlocked=!online || busy || state.jobs.some(j=>j.worker_id===stack.worker_id && !['passed','failed','pending','interrupted'].includes(j.status));
    const imagesReady=worker?.launchers?.image_workflow===1;
    const error=latest?.status==='failed' ? (latest.progress['virtual-operation']?.error || latest.reason || 'The last operation failed') : null;
    const captured=latest?.progress['virtual-operation']?.containers?.filter(c=>c.logs) || [];
    const roleFor=c=>({agent:'runner',daemon:'daemon',simulator:'sim'}[c.service] || c.service);
    const labelFor=role=>({runner:'Runner',daemon:'Daemon',sim:'Sim'}[role] || role);
    const selected=captured.find(c=>roleFor(c)===selectedContainerLogs.get(stack.id)) || captured[0];
    const containerOutput=selected ? `<div class="container-log-tabs" role="tablist" aria-label="Container logs">${captured.map(c=>`<button type="button" role="tab" aria-selected="${c===selected}" data-container-tab="${esc(roleFor(c))}">${esc(labelFor(roleFor(c)))}</button>`).join('')}</div><div role="tabpanel" aria-label="${esc(labelFor(roleFor(selected)))} logs"><p class="run-meta">${esc(selected.Names)} · last 120 lines${selected.logs.returncode ? ' · Docker logs failed (exit '+esc(selected.logs.returncode)+')' : ''}</p><p class="log-stream-label">stdout</p><pre>${esc(latest.files[selected.logs.stdout] || 'No stdout output')}</pre><p class="log-stream-label">stderr</p><pre>${esc(latest.files[selected.logs.stderr] || 'No stderr output')}</pre></div>` : `<pre>${esc(latest?.log || 'No operations yet. Click Start to launch this stack.')}</pre>`;

    const rows=[['runner','Runner','Agent service'],['daemon','Daemon',''],['sim','Sim',stack.topology==='singleton'?'Existing Base101 setup':'']].map(([key,label,hint])=>{
      const c=components[key] || {state:'not_started'};
      const status=unavailable ? 'Unknown' : ({running:'Running',not_started:'Not started',exited:'Stopped',created:'Created',external:'External',restarting:'Restarting'}[c.state] || c.state);
      const portText=key==='runner' ? (ports.agent ? `Port :${ports.agent}` : '') : key==='daemon' ? (ports.daemon ? `Runtime :${ports.daemon} · Status :${ports.status}${ports.development ? ` · Dev :${ports.development}` : ''}` : '') : stack.topology==='singleton' ? 'External network' : 'Internal network';
      return `<div class="stack-component"><div><strong>${label}</strong>${hint?`<span>${esc(hint)}</span>`:''}<span class="stack-ports">${esc(portText)}</span></div><div class="component-tools"><span class="component-state ${!unavailable&&c.state==='running'?'ready':''}">${esc(status)}</span><button class="component-logs" data-stack-action="logs" data-log-role="${key}" aria-label="${label} logs" ${actionBlocked || !c.container?'disabled':''}>Logs</button></div></div>`;
    }).join('');
    return `<article class="panel stack-card" data-stack="${stack.id}"><div class="stack-card-head"><div><h3>${esc(stackTitle(stack))}</h3><p>${esc(worker?.label || 'Worker disconnected')}</p></div><span class="badge ${busy?'running':!unavailable&&allRunning?'passed':''}">${esc(overall)}</span></div><div class="stack-identity"><span>${esc(identityLabel)}</span><code>${esc(identityText)}</code></div><div class="stack-components">${rows}</div>${worker?.launchers?.inventory_error?`<p class="notice error">${esc(worker.launchers.inventory_error)}</p>`:''}${error?`<p class="notice error">Last action: ${esc(error)}</p>`:''}${online&&!imagesReady?'<p class="notice">Restart the development panel and reconnect this account worker to enable shared image builds.</p>':''}<div class="stack-card-actions"><button class="primary" data-stack-action="start" ${actionBlocked || !imagesReady?'disabled':''}>${allRunning?'Restart':'Start'}</button><button data-stack-action="build" title="Build shared images once for all stacks; running containers stay unchanged" ${actionBlocked || !imagesReady?'disabled':''}>Rebuild images</button><button data-stack-action="stop" ${actionBlocked || !Object.values(components).some(c=>c.container)?'disabled':''}>Stop</button><button data-stack-action="logs" ${actionBlocked?'disabled':''}>Logs</button><button class="delete-stack" data-stack-action="delete" title="Remove this stack and its card; device state and evidence volumes are retained" ${actionBlocked?'disabled':''}>Delete</button></div><p class="stack-updated">${stack.observed_at?`Observed ${esc(new Date(stack.observed_at).toLocaleTimeString())}`:'Not observed yet'}</p><details class="stack-output" data-stack="${stack.id}" ${busy||outputOpen.has(stack.id)||openedStackOutput.has(stack.id)?'open':''}><summary>Output</summary>${containerOutput}</details><details class="stack-settings" data-stack="${stack.id}" ${settingsOpen.has(stack.id)?'open':''}><summary>Settings</summary><p class="run-meta">${esc(stack.topology==='singleton'?'ringfour-virtual':`ringfour-virtual-${stack.index}`)}</p><label>Run on<select data-stack-worker ${busy?'disabled':''}>${options(capable.filter(w=>w.launchers.root===stack.root).map(w=>({id:w.id,label:w.label})),stack.worker_id,'Choose worker')}</select></label><label class="check-label"><input data-stack-dirty type="checkbox" ${stack.allow_dirty?'checked':''} ${busy?'disabled':''}> Allow rebuilding modified sources</label><button data-stack-action="daemon" ${actionBlocked || !imagesReady?'disabled':''}>Restart daemon</button></details></article>`;
  }).join('');
  for(const card of $('#stack-cards').querySelectorAll('[data-stack].stack-card')) {
    for(const button of card.querySelectorAll('[data-stack-action]')) button.onclick=()=>runStack(card.dataset.stack,button.dataset.stackAction,button.dataset.logRole);
    for(const button of card.querySelectorAll('[data-container-tab]')) button.onclick=()=>{selectedContainerLogs.set(card.dataset.stack,button.dataset.containerTab);renderStacks();};
    for(const field of card.querySelectorAll('[data-stack-worker],[data-stack-dirty]')) field.onchange=async()=>{
      clearError();
      try {await api('/api/stacks/configure',{stack_id:card.dataset.stack,worker_id:card.querySelector('[data-stack-worker]').value,allow_dirty:card.querySelector('[data-stack-dirty]').checked});await refresh();}
      catch(error){failure(error);}
    };
    card.querySelector('.stack-output').ontoggle=event=>{if(!event.target.open)openedStackOutput.delete(card.dataset.stack);};
  }
}
async function runStack(stackId, action, logRole) {
  clearError();
  if(logRole)selectedContainerLogs.set(stackId,logRole);
  try {openedStackOutput.add(stackId);await api('/api/stacks/action',{stack_id:stackId,action});await refresh();}
  catch(error){failure(error);}
}
$('#add-stack').onclick=async()=>{
  clearError();$('#add-stack').disabled=true;
  try {
    const worker=state.workers.find(w=>w.online && w.launchers?.available && !w.launchers.inventory_error) || state.workers.find(w=>w.online && w.launchers?.available);
    if(!worker)throw new Error('Connect a launcher worker in Setup first.');
    const {stack}=await api('/api/stacks',{worker_id:worker.id});
    await refresh();
    $(`#stack-cards [data-stack="${stack.id}"]`)?.scrollIntoView({block:'nearest',behavior:'smooth'});
  } catch(error){failure(error);$('#add-stack').disabled=false;}
};
