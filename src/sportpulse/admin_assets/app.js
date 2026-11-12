(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const state = {csrf: null, role: null};
  const currency = n => Number(n).toLocaleString('fr-FR',{style:'currency',currency:'EUR'});
  const percent = n => (Number(n) * 100).toLocaleString('fr-FR',{maximumFractionDigits:2}) + ' %';
  function notify(message, error=false) {
    const box=$('message');box.textContent=message;box.classList.toggle('error',error);box.classList.remove('hidden');
  }
  async function api(url, opts={}) {
    const headers={'Accept':'application/json', ...(opts.body ? {'Content-Type':'application/json'}:{}),...opts.headers};
    if (opts.method && opts.method!=='GET' && url!=='/admin/api/login' && url!=='/admin/api/csrf') {
      const fresh=await fetch('/admin/api/csrf',{method:'POST',credentials:'same-origin'});
      const info=await fresh.json();
      if(!fresh.ok)throw new Error(info.detail||'Session administrateur expirée.');
      state.csrf=info.csrf_token;headers['X-CSRF-Token']=state.csrf;
    }
    let res;
    try {res=await fetch(url,{credentials:'same-origin',...opts,headers});}
    catch(e){throw new Error('Serveur indisponible. Vérifie Docker et la connexion locale.');}
    let data={};
    try {data=await res.json();}catch(e){}
    if (!res.ok) throw new Error(typeof data.detail==='string'?data.detail:'Erreur HTTP '+res.status);
    return data;
  }
  function showAdmin(profile) {
    $('login-view').classList.add('hidden');$('dashboard').classList.remove('hidden');
    $('logout').classList.remove('hidden');$('who').textContent=profile.email;
    state.role=profile.role; $('role').textContent=profile.role==='admin'?'Administrateur RH':'Lecture seule RH';
    $('simulate').disabled=profile.role!=='admin';
    $('audit-panel').classList.toggle('hidden',profile.role!=='admin');
  }
  function showLogin() {
    state.csrf=null;state.role=null;$('login-view').classList.remove('hidden');
    $('dashboard').classList.add('hidden');$('logout').classList.add('hidden');$('who').textContent='';
  }
  function showNumbers(data) {
    $('metric-total').textContent=String(data.employees_reviewed);
    $('metric-eligible').textContent=String(data.eligible_candidates);
    $('metric-before').textContent=currency(data.previous_budget_eur);
    $('metric-after').textContent=currency(data.new_budget_eur);
    $('metric-rate').textContent='Simulation au taux de '+percent(data.simulated_rate);
    $('delta').textContent=(Number(data.budget_delta_eur)>=0?'+':'')+currency(data.budget_delta_eur);
    $('notice').textContent=data.source_label+' · '+data.warning+' · Règles : '+data.policy_status;
  }
  function tableRow(table, cells) {
    const row=document.createElement('tr');
    cells.forEach(value=>{const td=document.createElement('td');td.textContent=String(value ?? '—');row.append(td);});
    table.append(row);
  }
  async function refreshHistory() {
    const data=await api('/admin/api/simulations');
    const target=$('history-rows');target.replaceChildren();
    if(!data.items.length){tableRow(target,['Aucune simulation enregistrée','','','','','','']);return;}
    data.items.forEach(r=>{
      tableRow(target,[new Date(r.created_at).toLocaleString('fr-FR'),r.run_id,r.source_label,r.reference_year,percent(r.previous_rate),percent(r.simulated_rate),currency(r.budget_delta)]);
      const td=document.createElement('td');
      if(state.role==='admin'){
        const b=document.createElement('button');b.type='button';b.className='sp13-history-action';b.textContent='Retirer';
        b.dataset.sp13Archive='simulation';b.dataset.sp13Ref=r.run_id;td.append(b);
      }
      target.lastElementChild.append(td);
    });
  }
  async function refreshAudit() {
    if(state.role!=='admin')return;
    const data=await api('/admin/api/audit'); const tbody=$('audit-rows');tbody.replaceChildren();
    data.items.forEach(r=>{
      tableRow(tbody,[new Date(r.happened_at).toLocaleString('fr-FR'),r.action,r.object_ref]);
      const td=document.createElement('td');
      if(state.role==='admin' && r.event_id!=null){
        const b=document.createElement('button');b.type='button';b.className='sp13-history-action';b.textContent='Retirer';
        b.dataset.sp13Archive='audit';b.dataset.sp13Ref=String(r.event_id);td.append(b);
      }
      tbody.lastElementChild.append(td);
    });
  }
  async function overview() {
    const source=$('source').value, year=Number($('year').value);
    showNumbers(await api('/admin/api/overview?source='+encodeURIComponent(source)+'&reference_year='+encodeURIComponent(year)));
  }
  $('login-form').addEventListener('submit',async e=>{
    e.preventDefault();
    try {
      const data=await api('/admin/api/login',{method:'POST',body:JSON.stringify({email:$('email').value,password:$('password').value})});
      state.csrf=data.csrf_token;$('password').value='';
      const profile=await api('/admin/api/me');showAdmin(profile);
      await Promise.all([overview(),refreshHistory(),refreshAudit()]);
      notify('Connexion réussie.');
    } catch(err){notify(err.message,true);}
  });
  $('logout').addEventListener('click',async()=>{
    try {await api('/admin/api/logout',{method:'POST'});}catch(e){notify(e.message,true);}
    showLogin();
  });
  $('refresh').addEventListener('click',()=>overview().catch(e=>notify(e.message,true)));
  $('history-refresh').addEventListener('click',()=>refreshHistory().catch(e=>notify(e.message,true)));
  $('audit-refresh').addEventListener('click',()=>refreshAudit().catch(e=>notify(e.message,true)));
  $('scenario-form').addEventListener('submit',async e=>{
    e.preventDefault();
    try {
      const data=await api('/admin/api/simulations',{method:'POST',body:JSON.stringify({
        source:$('source').value,reference_year:Number($('year').value),bonus_rate_percent:Number($('rate').value)
      })});
      showNumbers(data); await Promise.all([refreshHistory(),refreshAudit()]);
      notify('Scénario enregistré : '+data.run_id+' (sans effet sur les règles RH).');
    }catch(err){notify(err.message,true);}
  });
  (async()=>{
    try {
      const profile=await api('/admin/api/me');
      state.csrf=(await api('/admin/api/csrf',{method:'POST'})).csrf_token;
      showAdmin(profile);
      await Promise.all([overview(),refreshHistory(),refreshAudit()]);
    }catch(e){showLogin();}
  })();
})();
