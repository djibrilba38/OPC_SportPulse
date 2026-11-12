/* SportPulse 15 : comptes RH et replay intégrés, aucun QR ni code de validation. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = { rules: [] };
  const option = (value, label) => {const x=document.createElement('option');x.value=value;x.textContent=label;return x;};
  const euros = x => Number(x || 0).toLocaleString('fr-FR',{style:'currency',currency:'EUR'});
  const pct = x => (Number(x)*100).toLocaleString('fr-FR',{maximumFractionDigits:2})+' %';
  const info = (message, bad=false, id='sp15-accounts-status') => {
    const target=$(id); if (!target) return;
    target.hidden=false;target.textContent=message;target.classList.toggle('error',bad);
  };
  async function get(url) {
    const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});
    let j={};try{j=await r.json();}catch{}
    if(!r.ok) throw Error(j.detail||`HTTP ${r.status}`);
    return j;
  }
  async function post(url, body) {
    // Existing portal CSRF rotates on POST; use POST to acquire fresh token.
    const tokenRequest=await fetch('/admin/api/csrf',{method:'POST',credentials:'same-origin'});
    const token=await tokenRequest.json();
    if(!tokenRequest.ok)throw Error(token.detail||'Session expirée');
    const r=await fetch(url,{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json','X-CSRF-Token':token.csrf_token},body:JSON.stringify(body)});
    let j={};try{j=await r.json()}catch{};
    if(!r.ok)throw Error(j.detail||`HTTP ${r.status}`);
    return j;
  }
  function year(){return Number($('year')?.value)||2026;}
  async function accounts(){
    const search=($('sp15-search')?.value||'').trim();
    const data=await get(`/admin/api/rh/portal-accounts?year=${year()}&search=${encodeURIComponent(search)}`);
    const target=$('sp15-accounts-list');if(!target)return;target.replaceChildren();
    for(const employee of data.items){
      const row=document.createElement('div');row.className='sp15-account-row';
      const name=document.createElement('strong');name.textContent=employee.name||employee.employee_id;
      const id=document.createElement('span');id.textContent=employee.employee_id;
      const label=document.createElement('small');
      label.textContent=employee.linked_rh?'Compte lié au référentiel RH':employee.is_demo?'Compte de démonstration':'Lien RH à synchroniser';
      row.append(name,id,label);target.append(row);
    }
    if(!data.items.length)target.textContent='Aucun compte inscrit ne correspond à cette recherche.';
    $('sp15-count').textContent=`${data.total} compte(s) créé(s) · ${data.rh_reference_count} collaborateurs dans le référentiel RH`;
  }
  async function sync(){
    const data=await post('/admin/api/rh/portal-accounts/sync',{reference_year:year()});
    info(`${data.created_links} liaison(s) ajoutée(s), ${data.unmatched.length} compte(s) non rapproché(s).`);
    await accounts();
  }
  async function invite(e){
    e.preventDefault();
    const data=await post('/admin/api/rh/invitations',{
      employee_id:$('sp15-invite-id').value.trim(),display_name:$('sp15-invite-name').value.trim(),reference_year:year()
    });
    const box=$('sp15-invite-result');box.hidden=false;
    $('sp15-invite-token').value=data.invitation_code;
    info('Invitation créée. Communique-la en privé au salarié (pas sur Slack).');
  }
  async function rules(){
    const data=await get('/admin/api/rh/replay/rules');state.rules=data.items||[];
    const select=$('sp14-rule');if(!select)return;const keep=select.value;select.replaceChildren();
    for(const r of state.rules)select.append(option(String(r.version),`Version ${r.version} — ${pct(r.bonus_rate)}`));
    if(state.rules.some(r=>String(r.version)===keep))select.value=keep;
  }
  async function runs(){
    const data=await get('/admin/api/rh/replay/runs');const e=$('sp14-history');if(!e)return;
    e.replaceChildren();
    for(const run of (data.items||[]).slice(0,5)){
      const row=document.createElement('div');row.className='sp14-history-row';
      const text=document.createElement('div');
      text.textContent=`${new Date(run.created_at).toLocaleString('fr-FR')} · v${run.rule_version} · ${euros(run.result?.simulated_bonus_budget_eur)} · ${run.parent_run_id?'Replay':'Simulation'}`;
      const button=document.createElement('button');button.type='button';button.textContent='Rejouer';
      button.addEventListener('click',()=>replayOne(run.run_id));
      row.append(text,button);e.append(row);
    }
    if(!e.children.length)e.textContent='Aucun calcul enregistré.';
  }
  function showRun(data){
    const el=$('sp14-run-result');el.hidden=false;
    el.textContent=`Budget simulé : ${euros(data.simulated_bonus_budget_eur)} · Écart : ${euros(data.delta_to_reference_eur)} · Règle v${data.rule_version}. Aucun paiement.`;
  }
  async function replayOne(id){
    try {const data=await post(`/admin/api/rh/replay/runs/${encodeURIComponent(id)}/replay`,{
      rule_version:Number($('sp14-rule').value)
    });showRun(data);await runs();info('Replay terminé.',false,'sp14-replay-status');}
    catch(e){info(e.message,true,'sp14-replay-status');}
  }
  window.addEventListener('DOMContentLoaded',()=>{
    $('sp15-search-button')?.addEventListener('click',()=>accounts().catch(e=>info(e.message,true)));
    $('sp15-sync')?.addEventListener('click',()=>sync().catch(e=>info(e.message,true)));
    $('sp15-invite-form')?.addEventListener('submit',e=>invite(e).catch(err=>info(err.message,true)));
    $('sp14-rule-form')?.addEventListener('submit',async e=>{
      e.preventDefault();try{const rate=Number($('sp14-new-rate').value);
        const data=await post('/admin/api/rh/replay/rules',{rate_percent:rate,note:'Simulation RH — POC'});
        await rules();$('sp14-rule').value=String(data.version);
        info('Nouvelle règle enregistrée.',false,'sp14-replay-status');
      }catch(err){info(err.message,true,'sp14-replay-status');}
    });
    $('sp14-run-form')?.addEventListener('submit',async e=>{
      e.preventDefault();try{const r=await post('/admin/api/rh/replay/runs',{
        source:'postgres',reference_year:year(),rule_version:Number($('sp14-rule').value)
      });showRun(r);await runs();info('Simulation enregistrée.',false,'sp14-replay-status');}
      catch(err){info(err.message,true,'sp14-replay-status');}
    });
    $('sp14-refresh-history')?.addEventListener('click',()=>runs().catch(e=>info(e.message,true,'sp14-replay-status')));
    $('sp15-search')?.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();accounts().catch(e=>info(e.message,true));}});
    accounts().catch(e=>info(e.message,true));
    Promise.all([rules(),runs()]).catch(e=>info(e.message,true,'sp14-replay-status'));
  });
})();
