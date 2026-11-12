'use strict';
const $=id=>document.getElementById(id);
let csrf=null,activeRun=null;
const eur=n=>Number(n).toLocaleString('fr-FR',{style:'currency',currency:'EUR'});
const pct=n=>(100*Number(n)).toLocaleString('fr-FR',{maximumFractionDigits:2})+' %';
function banner(msg,error=false){const el=$('banner');el.textContent=msg;el.className='notice '+(error?'bad':'ok');}
async function api(url,method='GET',body){
  const headers={'Accept':'application/json'};
  if(body!==undefined)headers['Content-Type']='application/json';
  if(method!=='GET'&&csrf)headers['X-CSRF-Token']=csrf;
  const response=await fetch(url,{method,credentials:'same-origin',headers,body:body===undefined?undefined:JSON.stringify(body)});
  const payload=await response.json().catch(()=>({}));
  if(!response.ok){const d=payload.detail;throw new Error(typeof d==='string'?d:`Erreur HTTP ${response.status}`)}
  return payload;
}
function renderResult(data){activeRun=data.run_id;const row=data.result||data;
  $('old-budget').textContent=eur(row.reference_bonus_budget_eur);$('new-budget').textContent=eur(row.simulated_bonus_budget_eur);
  const d=Number(row.delta_to_reference_eur);$('budget-delta').textContent=(d>=0?'+':'')+eur(d);$('eligible').textContent=row.eligible_candidates;
  $('run-label').textContent=`Run ${data.run_id} · règle v${data.rule_version} · source ${String(data.source_fingerprint||'').slice(0,12)}…`;
  $('result-note').textContent=row.warning+' · Cette simulation ne modifie aucune prime officielle.';
}
async function loadRules(){const r=await api('/admin/api/replay/rules');const s=$('rule');s.replaceChildren();
  r.items.forEach(o=>{const e=document.createElement('option');e.value=o.version;e.textContent=`v${o.version} — ${pct(o.bonus_rate)} · ${o.note}`;s.append(e)});
  if(r.items.length)s.value=r.items.at(-1).version;
}
async function loadRuns(){const r=await api('/admin/api/replay/runs'),t=$('runs');t.replaceChildren();
  if(!r.items.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=7;td.textContent='Aucune exécution';tr.append(td);t.append(tr);return;}
  for(const item of r.items){const tr=document.createElement('tr');const v=[item.run_id,item.reference_year,item.source_kind,'v'+item.rule_version,eur(item.result.simulated_bonus_budget_eur),eur(item.result.delta_to_reference_eur)];
    for(const x of v){const td=document.createElement('td');td.textContent=x;tr.append(td)}
    const td=document.createElement('td'),btn=document.createElement('button');btn.textContent='Rejouer';btn.disabled=false;
    btn.onclick=async()=>{try{const run=await api('/admin/api/replay/runs/'+encodeURIComponent(item.run_id)+'/replay','POST',{rule_version:Number($('rule').value)});renderResult(run);await loadRuns();banner('Rejeu enregistré. Les données sources d’origine ont été conservées.')}catch(e){banner(e.message,true)}};
    td.append(btn);tr.append(td);t.append(tr);
  }
}
async function boot(){try{await api('/admin/api/me');csrf=(await api('/admin/api/csrf','POST')).csrf_token;
  $('workspace').hidden=false;await Promise.all([loadRules(),loadRuns()]);banner('Connexion RH reconnue. Choisis une version et lance un calcul.');}
  catch(e){banner('Connecte-toi sur /admin avant d’utiliser cet espace : '+e.message,true);}}
$('save-rule').onclick=async()=>{try{const obj=await api('/admin/api/replay/rules','POST',{rate_percent:Number($('rate').value),note:$('note').value});await loadRules();$('rule').value=obj.version;banner(`Règle v${obj.version} enregistrée. L’ancienne reste inchangée.`)}catch(e){banner(e.message,true)}};
$('run').onclick=async()=>{try{const obj=await api('/admin/api/replay/runs','POST',{source:$('source').value,reference_year:Number($('year').value),rule_version:Number($('rule').value)});renderResult(obj);await loadRuns();banner('Calcul sauvegardé avec empreinte de données et règles versionnées.')}catch(e){banner(e.message,true)}};
$('refresh').onclick=()=>loadRuns().catch(e=>banner(e.message,true));
boot();
