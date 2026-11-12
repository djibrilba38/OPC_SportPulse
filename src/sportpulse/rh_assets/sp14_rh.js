/* POC SportPulse: comptes réels du portail, preuve QR et replay dans /admin/rh.
   Les salaires restent dans les API RH existantes, jamais dans les sélecteurs QR. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {accounts:[],items:[],lastRun:null, rules:[]};
  const str = x => String(x == null ? '' : x);
  async function get(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});let j={};try{j=await r.json()}catch{}if(!r.ok)throw Error(j.detail||`HTTP ${r.status}`);return j;}
  async function write(url, body){
    const t = await getCSRF();
    const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':t},body:JSON.stringify(body)});
    let j={};try{j=await r.json()}catch{}if(!r.ok)throw Error(j.detail||`HTTP ${r.status}`);return j;
  }
  async function getCSRF(){const r=await fetch('/admin/api/csrf',{method:'POST',credentials:'same-origin'});const j=await r.json();if(!r.ok)throw Error(j.detail||'Connexion expirée');return j.csrf_token;}
  function alert(message, bad=false, id='sp14-activity-status'){const el=$(id);if(!el)return;el.hidden=false;el.classList.toggle('error',bad);el.textContent=message;}
  function option(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;return o;}
  function currentEmployee(){return $('sp14-account').value;}
  async function listAccounts(){
    const query=($('sp14-search').value||'').trim();const year=Number($('year').value)||2026;
    const data=await get(`/admin/api/rh/portal-accounts?year=${year}&search=${encodeURIComponent(query)}`);
    state.accounts=data.items;const el=$('sp14-account');const keep=el.value;el.replaceChildren(option('','Sélectionner un compte salarié…'));
    for(const a of state.accounts){
      const stateLabel=a.linked_rh?'RH relié':a.is_demo?'Compte démo du portail':'RH non rattaché';
      el.append(option(a.employee_id,`${a.name} · ${a.employee_id} · ${stateLabel}`));
    }
    if(state.accounts.some(a=>a.employee_id===keep))el.value=keep;
    $('sp14-account-count').textContent=`${data.total} compte(s) inscrits · ${data.rh_reference_count} IDs dans le référentiel RH. Les 161 salariés n'ont pas tous un compte portail.`;
    if(el.value)await listItems();else $('sp14-ref').replaceChildren(option('','Choisir un compte d’abord'));
  }
  async function listItems(){
    const id=currentEmployee(),select=$('sp14-ref');select.replaceChildren(option('','Choisir une activité / arrivée…'));
    if(!id)return;
    const data=await get('/admin/api/rh/portal-accounts/'+encodeURIComponent(id)+'/items');state.items=data.items;
    for(const it of data.items){select.append(option(it.ref,`${it.kind==='commute'?'Arrivée':'Activité'} : ${it.label} · ${it.ref}`));}
    const account=state.accounts.find(a=>a.employee_id===id);
    $('sp14-link').textContent=account?.linked_rh?'Rattaché à la version RH active':account?.is_demo?'Compte portail de démonstration : pas de rémunération RH':'Aucun lien RH validé';
    $('sp14-link').classList.toggle('warn',!account?.linked_rh);
    if(!data.items.length)alert('Aucune activité ou arrivée en attente. Crée une séance depuis ce compte salarié, puis actualise.',false);
    else $('sp14-activity-status').hidden=true;
  }
  async function issue(e){
    e.preventDefault();const id=currentEmployee(),ref=$('sp14-ref').value;
    if(!id||!ref)return alert('Choisis le compte puis la séance ou arrivée à valider.',true);
    const item=state.items.find(it=>it.ref===ref);if(!item)return alert('Actualise la sélection.',true);
    try{
      const result=await write('/admin/api/proofs/issue',{employee_id:id,activity_ref:ref,kind:item.kind});
      $('sp14-code').textContent=result.code;$('sp14-code-image').src=result.qr_data_url;
      $('sp14-code-result').hidden=false;
      alert('Code émis pour '+id+' · '+ref+' (5 minutes, une seule utilisation).');
    }catch(e){alert(e.message,true);}
  }
  async function invite(e){
    e.preventDefault();
    const body={employee_id:$('sp14-invite-id').value.trim(),display_name:$('sp14-invite-name').value.trim(),reference_year:Number($('year').value)};
    try{const r=await write('/admin/api/rh/invitations',body);$('sp14-invite-result').hidden=false;
      $('sp14-invite-token').value=r.invitation_code;alert('Invitation créée : à communiquer en privé au salarié.',false,'sp14-invite-status');
    }catch(e){alert(e.message,true,'sp14-invite-status');}
  }
  const pct=s=> (Number(s)*100).toLocaleString('fr-FR',{maximumFractionDigits:2})+' %';
  const eur=s=>Number(s||0).toLocaleString('fr-FR',{style:'currency',currency:'EUR'});
  async function rules(){const data=await get('/admin/api/rh/replay/rules');state.rules=data.items;
    const e=$('sp14-rule'),keep=e.value;e.replaceChildren();
    for(const r of state.rules){e.append(option(String(r.version),`Version ${r.version} — ${pct(r.bonus_rate)}`));}
    if(state.rules.some(r=>String(r.version)===keep))e.value=keep;
  }
  async function runs(){const data=await get('/admin/api/rh/replay/runs');const e=$('sp14-history');e.replaceChildren();
    if(!data.items.length){e.textContent='Aucun calcul versionné enregistré.';return;}
    for(const run of data.items){
      const row=document.createElement('div');row.className='sp14-history-row';
      const txt=document.createElement('div');const result=run.result||{};
      txt.textContent=`${new Date(run.created_at).toLocaleString('fr-FR')} · v${run.rule_version} · ${eur(result.simulated_bonus_budget_eur)} · ${run.parent_run_id?'Replay':'Calcul initial'}`;
      const btn=document.createElement('button');btn.type='button';btn.textContent='Rejouer';btn.addEventListener('click',()=>replayOne(run.run_id));
      row.append(txt,btn);e.append(row);
    }
  }
  function showRun(r){$('sp14-run-result').hidden=false;
    $('sp14-run-result').textContent=`Budget simulé : ${eur(r.simulated_bonus_budget_eur)} · Écart : ${eur(r.delta_to_reference_eur)} · Règle v${r.rule_version} · Référence : ${r.run_id}. SIMULATION UNIQUEMENT.`;
    state.lastRun=r;
  }
  async function createRule(e){e.preventDefault();const rate=Number($('sp14-new-rate').value);
    if(!Number.isFinite(rate)||rate<0||rate>20)return alert('Taux invalide (0–20 %).',true,'sp14-replay-status');
    try{const r=await write('/admin/api/rh/replay/rules',{rate_percent:rate,note:'Hypothèse de simulation créée depuis la vue RH'});
      await rules();$('sp14-rule').value=String(r.version);alert('Règle v'+r.version+' enregistrée, immuable.',false,'sp14-replay-status');
    }catch(err){alert(err.message,true,'sp14-replay-status');}
  }
  async function createRun(e){e.preventDefault();try{
    const r=await write('/admin/api/rh/replay/runs',{source:'postgres',reference_year:Number($('year').value),rule_version:Number($('sp14-rule').value)});
    showRun(r);await runs();alert('Nouveau calcul enregistré sans modifier la paie.',false,'sp14-replay-status');
  }catch(err){alert(err.message,true,'sp14-replay-status');}}
  async function replayOne(id){try{const r=await write(`/admin/api/rh/replay/runs/${encodeURIComponent(id)}/replay`,{rule_version:Number($('sp14-rule').value)});
    showRun(r);await runs();alert('Historique rejoué depuis son instantané initial.',false,'sp14-replay-status');
  }catch(err){alert(err.message,true,'sp14-replay-status');}}
  window.addEventListener('DOMContentLoaded',()=>{
    $('sp14-search-button')?.addEventListener('click',()=>listAccounts().catch(e=>alert(e.message,true)));
    $('sp14-sync')?.addEventListener('click',async()=>{try{const r=await write('/admin/api/rh/portal-accounts/sync',{reference_year:Number($('year').value)});await listAccounts();alert(`${r.created_links} nouveau(x) lien(s) RH. ${r.unmatched.length} compte(s) non rapproché(s).`);}catch(e){alert(e.message,true);}});
    $('sp14-account')?.addEventListener('change',()=>listItems().catch(e=>alert(e.message,true)));
    $('sp14-refresh-items')?.addEventListener('click',()=>listItems().catch(e=>alert(e.message,true)));
    $('sp14-proof-form')?.addEventListener('submit',issue);
    $('sp14-invite-form')?.addEventListener('submit',invite);
    $('sp14-rule-form')?.addEventListener('submit',createRule);
    $('sp14-run-form')?.addEventListener('submit',createRun);
    $('sp14-refresh-history')?.addEventListener('click',()=>runs().catch(e=>alert(e.message,true,'sp14-replay-status')));
    $('sp14-account')?.add(option('','Chargement…'));
    listAccounts().catch(e=>alert(e.message,true));
    Promise.all([rules(),runs()]).catch(e=>alert(e.message,true,'sp14-replay-status'));
  });
})();
