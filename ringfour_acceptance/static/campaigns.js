'use strict';
let editingEnvironment = null, environmentDirty = false, environmentReady = false;
const roleNames = {A:'Publisher A',B:'Consumer B · separate user and organization',C:'Second robot in A’s organization · optional',Operator:'Operator in A’s organization · optional'};
function editEnvironment(environment) {
  editingEnvironment=environment ? structuredClone(environment) : null;
  $('#environment-editor').open=!environment;
  $('#environment-name').value=environment?.name || 'Virtual AP environment';
  $('#environment-build').value=environment?.build_label || 'Local build';
  $('#environment-api').value=environment?.api_url || state.workers.find(w=>w.identity.api_url)?.identity.api_url || 'http://localhost:8080';
  $('#environment-repository').value=environment?.repository || state.setup.repository || '127.0.0.1:5000/ringfour/acceptance';
  $('#environment-regression').value=environment?.regression_runner || '';
  $('#environment-official').checked=!!environment?.official_publisher;
  $('#environment-entrypoint').value=environment?.robot_check_entrypoint || '';
  $('#environment-roles').innerHTML=Object.entries(roleNames).map(([name,title])=>{
    const role=environment?.roles[name] || {};
    return `<fieldset class="campaign-role" data-role="${name}"><legend>${title}</legend>${['C','Operator'].includes(name)?`<label class="check-label"><input data-role-enabled type="checkbox" ${environment?.roles[name]?'checked':''}> Include this role</label>`:''}<label>Account runner<select data-role-worker>${options(state.workers.map(w=>({id:w.label,label:workerName(w)})),role.runner_label,'Select or enter label below')}</select></label><label>Runner label<input data-role-field="runner_label" list="runner-labels" value="${esc(role.runner_label)}"></label><label>User UUID<input data-role-field="user_id" value="${esc(role.user_id)}"></label><label>Organization UUID<input data-role-field="org_id" list="orgs-${name}" value="${esc(role.org_id)}"></label><datalist id="orgs-${name}"></datalist><label>Robot UUID${name==='Operator'?' (optional)':''}<input data-role-field="robot_id" list="robots-${name}" value="${esc(role.robot_id)}"></label><datalist id="robots-${name}"></datalist><label>Stack binding (optional)<select data-role-field="stack_id">${options((state.stacks||[]).map(s=>({id:s.id,label:`${stackTitle(s)} · ${s.robot_id || 'not enrolled'}`})),role.stack_id,'Verify against API only')}</select></label><details><summary>Registry override</summary><input aria-label="${name} registry override" data-role-field="repository" value="${esc(role.repository)}" placeholder="Use shared prefix"></details></fieldset>`;
  }).join('');
  for(const fieldset of $('#environment-roles').querySelectorAll('fieldset')) {
    const field=name=>fieldset.querySelector(`[data-role-field="${name}"]`);
    // Editing an optional role means including it. Explicit unchecking still
    // excludes it until the operator edits its fields again.
    const includeEditedRole=event=>{
      if(event.target.matches('[data-role-field], [data-role-worker]')) {
        const enabled=fieldset.querySelector('[data-role-enabled]');
        if(enabled)enabled.checked=true;
      }
    };
    fieldset.addEventListener('input',includeEditedRole);
    fieldset.addEventListener('change',includeEditedRole);
    const suggestions=()=>{
      const worker=state.workers.find(w=>w.label===field('runner_label').value);
      const name=fieldset.dataset.role;
      $(`#orgs-${name}`).innerHTML=(worker?.identity.organizations||[]).map(o=>`<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('');
      const org=worker?.identity.organizations?.find(o=>o.id===field('org_id').value);
      $(`#robots-${name}`).innerHTML=(org?.robots||[]).map(r=>`<option value="${esc(r.id)}">${esc(r.name)}</option>`).join('');
    };
    fieldset.querySelector('[data-role-worker]').onchange=event=>{
      field('runner_label').value=event.target.value;
      const worker=state.workers.find(w=>w.label===event.target.value);
      field('user_id').value=worker?.identity.user?.id || '';
      const org=worker?.identity.organizations?.length===1 ? worker.identity.organizations[0] : null;
      field('org_id').value=org?.id || '';field('robot_id').value=org?.robots?.length===1 ? org.robots[0].id : '';
      environmentDirty=true;suggestions();
    };
    field('org_id').oninput=suggestions;field('runner_label').oninput=suggestions;
    field('stack_id').onchange=()=>{
      const stack=state.stacks.find(s=>s.id===field('stack_id').value);
      if(stack?.robot_id)field('robot_id').value=stack.robot_id;
    };
    suggestions();
  }
  environmentDirty=false;$('#environment-saved').textContent='';renderEnvironmentGuide();
}
$('#environment-form').oninput=()=>{environmentDirty=true;$('#environment-saved').textContent='Unsaved changes';};
$('#environment-form').onchange=()=>{environmentDirty=true;};
$('#new-environment').onclick=()=>{editEnvironment(null);$('#environment-select').value='';};
$('#environment-select').onchange=()=>editEnvironment((state.environments||[]).find(e=>e.id===$('#environment-select').value));
async function saveEnvironment() {
  const roles={};
  for(const fieldset of $('#environment-roles').querySelectorAll('fieldset')) {
    if(fieldset.querySelector('[data-role-enabled]') && !fieldset.querySelector('[data-role-enabled]').checked)continue;
    roles[fieldset.dataset.role]=Object.fromEntries([...fieldset.querySelectorAll('[data-role-field]')].map(i=>[i.dataset.roleField,i.value.trim()]));
  }
  const environment=await api('/api/environments',{id:editingEnvironment?.id,name:$('#environment-name').value,build_label:$('#environment-build').value,api_url:$('#environment-api').value,repository:$('#environment-repository').value,regression_runner:$('#environment-regression').value,roles,official_publisher:$('#environment-official').checked,robot_check_entrypoint:$('#environment-entrypoint').value});
  editingEnvironment=environment;environmentDirty=false;$('#environment-saved').textContent='Saved';$('#environment-saved').title='Included roles: '+Object.keys(roles).join(', ');await refresh();return environment;
}
$('#environment-form').onsubmit=async event=>{event.preventDefault();clearError();try{await saveEnvironment();}catch(error){failure(error);}};
$('#launch-campaign').onclick=async()=>{
  clearError();$('#launch-campaign').disabled=true;
  try {const environment=environmentDirty || !editingEnvironment ? await saveEnvironment() : editingEnvironment;await api('/api/campaigns',{environment_id:environment.id,scope:$('#campaign-scope').value});await refresh();}
  catch(error){failure(error);}finally{$('#launch-campaign').disabled=false;}
};
function renderEnvironmentGuide() {
  const environment=editingEnvironment;
  $('#environment-guide-title').textContent=`Set up ${environment?.name || 'your AP environment'}`;
  const roles=environment?.roles || {};
  const workerFor=role=>state.workers.find(w=>w.label===role?.runner_label && w.identity.user?.id===role.user_id && w.online);
  const conflicts=[];
  for(const [name,other] of [['A','B'],['B','A']]) {
    const worker=workerFor(roles[name]);
    const organization=worker?.identity.organizations?.find(o=>o.id===roles[other]?.org_id);
    if(organization)conflicts.push(`${roles[name].runner_label} (${name}) can already access ${organization.name || organization.id}, the test organization for ${other}.`);
  }
  const warning=$('#environment-access-warning');
  warning.hidden=!conflicts.length;
  warning.textContent=conflicts.length ? `Organization privacy needs attention: ${conflicts.join(' ')} Choose test organizations whose memberships do not overlap before starting a new campaign.` : '';
  $('#environment-guide-targets').innerHTML=Object.keys(roles).length ? `<div class="environment-target-table"><table><thead><tr><th>Role</th><th>Account runner</th><th>Organization</th><th>Robot / stack</th></tr></thead><tbody>${Object.entries(roles).map(([name,role])=>{
    const worker=workerFor(role);
    const org=worker?.identity.organizations?.find(o=>o.id===role.org_id);
    const robot=org?.robots?.find(r=>r.id===role.robot_id);
    const stack=(state.stacks||[]).find(s=>s.id===role.stack_id);
    return `<tr><td>${esc(name)}</td><td>${esc(role.runner_label)}<small>${esc(role.user_id)}</small></td><td>${esc(org?.name || 'Organization')}<small>${esc(role.org_id)}</small></td><td>${esc([robot?.name,stack?.name].filter(Boolean).join(' · ') || 'Robot')}<small>${esc(role.robot_id || 'Not selected')}</small></td></tr>`;
  }).join('')}</tbody></table></div>` : '<p class="help">Save an environment to see its selected account, organization and robot IDs here.</p>';
}
function renderCampaigns() {
  const environments=state.environments||[],campaigns=state.campaigns||[];
  $('#environment-select').innerHTML=options(environments,editingEnvironment?.id,'New environment');
  $('#runner-labels').innerHTML=state.workers.map(w=>`<option value="${esc(w.label)}">${esc(workerName(w))}</option>`).join('');
  if(!environmentReady){editEnvironment(environments[0]);environmentReady=true;$('#environment-select').value=editingEnvironment?.id||'';}
  renderEnvironmentGuide();
  for(const fieldset of $('#environment-roles').querySelectorAll('fieldset')) {
    const selector=fieldset.querySelector('[data-role-worker]');
    if(document.activeElement!==selector)selector.innerHTML=options(state.workers.map(w=>({id:w.label,label:workerName(w)})),fieldset.querySelector('[data-role-field="runner_label"]').value,'Select or enter label below');
  }
  // Preserve an operator's in-progress evidence entry while polling.
  if(document.activeElement?.closest('#campaign-history form'))return;
  const open=new Set([...$('#campaign-history').querySelectorAll('details[open]')].map(d=>d.dataset.case));
  $('#campaign-history').innerHTML=campaigns.length ? campaigns.map(c=>{
    const active=['running','waiting_runner','cancelling'].includes(c.status);
    const terminal=c.cases.filter(k=>!['waiting','waiting_runner','queued','running'].includes(k.status)).length;
    const rows=c.cases.map(k=>{
      const job=state.jobs.find(j=>j.id===k.job_id),report=job?.progress?.['case-report'];
      return `<details class="campaign-case" data-case="${c.id}:${k.id}" ${open.has(`${c.id}:${k.id}`)?'open':''}><summary><span>${esc(k.title)}<small>${esc(k.ap)} · ${esc(k.kind)}</small></span><span class="badge ${esc(k.status)}">${esc(k.status.replaceAll('_',' '))}</span></summary><div class="case-body">${k.reason?`<p>${esc(k.reason)}</p>`:''}${k.instructions?`<p>${esc(k.instructions)}</p>`:''}${report?.checks?.length?`<ul>${report.checks.map(check=>`<li>${esc(check)}</li>`).join('')}</ul>`:''}${job?`<p><a href="/api/runs/${job.id}/report.json" target="_blank" rel="noopener">Job evidence</a> · ${esc(job.worker_label)}</p><pre>${esc(job.log || 'Waiting for output…')}</pre>`:''}${k.operator_evidence?`<p>Recorded by ${esc(k.operator_evidence.operator)} · ${esc(k.operator_evidence.recorded_at)}</p><p>${esc(k.operator_evidence.reference)}</p><p>${esc(k.operator_evidence.note)}</p>`:''}${k.kind==='operator' && k.status!=='deferred'?`<form data-evidence="${c.id}" data-case-id="${k.id}"><label>Operator<input name="operator" required></label><label>Evidence URL or file path<input name="reference" required></label><label>Notes<textarea name="note"></textarea></label><label>Outcome<select name="outcome"><option value="manual_pass">Manual pass</option><option value="failed">Failed</option></select></label><button>Record evidence</button></form>`:''}</div></details>`;
    }).join('');
    return `<article class="panel campaign-card"><div class="section-heading"><div><h2>${esc(c.environment.name)}</h2><p class="muted">${esc(c.environment.build_label)} · ${esc(c.scope)} · ${esc(new Date(c.created_at).toLocaleString())}</p></div><span class="badge ${esc(c.status)}">${esc(c.status.replaceAll('_',' '))}</span></div><p>${terminal} / ${c.cases.length} cases evaluated · Automated checks ${c.automated_passed?'passed':'incomplete'} · Full AP acceptance remains open</p><p class="help">${esc(Object.entries(c.summary).map(([s,n])=>`${n} ${s.replaceAll('_',' ')}`).join(' · '))}</p><div class="actions"><a href="/api/campaigns/${c.id}/report.md" download>Markdown report</a><a href="/api/campaigns/${c.id}" target="_blank" rel="noopener">JSON evidence</a>${active?`<button data-campaign-action="cancel" data-campaign="${c.id}">Cancel and clean fixtures</button>`:`<button data-campaign-action="cleanup" data-campaign="${c.id}" ${c.status==='passed'?'disabled':''}>Recover fixture cleanup</button>`}</div><details><summary>Recorded environment and source builds</summary><pre>${esc(JSON.stringify({environment:c.environment,builds:c.builds},null,2))}</pre></details><div>${rows}</div></article>`;
  }).join('') : '<div class="panel"><h2>Ready for the next build</h2><p>Configure A and B with two authenticated accounts, two organizations and two independent robots. Preflight checks ownership, idle state and stack identity before any App is installed.</p><p>Optional C exercises a second robot in A’s organization. An additional Operator account tests restricted permissions. Missing roles, official publishing and operator gates remain visible in the report.</p></div>';
  for(const button of $('#campaign-history').querySelectorAll('[data-campaign-action]'))button.onclick=async()=>{clearError();try{await api(`/api/campaigns/${button.dataset.campaignAction}`,{campaign_id:button.dataset.campaign});await refresh();}catch(error){failure(error);}};
  for(const form of $('#campaign-history').querySelectorAll('[data-evidence]'))form.onsubmit=async event=>{event.preventDefault();clearError();try{await api('/api/campaigns/evidence',{campaign_id:form.dataset.evidence,case_id:form.dataset.caseId,...Object.fromEntries(new FormData(form))});document.activeElement?.blur();await refresh();}catch(error){failure(error);}};
}
