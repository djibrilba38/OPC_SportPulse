'use strict';
let editingActivity = null;

const $ = id => document.getElementById(id);
const state = {authMode: 'login', csrf: null, data: null, page: 'dashboard', toastTimeout: null};
const money = n => n == null ? 'À confirmer' : new Intl.NumberFormat('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:0}).format(n);
const nf = n => new Intl.NumberFormat('fr-FR',{maximumFractionDigits:1}).format(n);
const dateLabel = s => {const d=new Date(s);return Number.isNaN(d.getTime()) ? 'Date inconnue' : d.toLocaleDateString('fr-FR',{day:'numeric',month:'short',year:'numeric'});};
const sports = {running:'🏃',cycling:'🚴',walking:'🚶',hiking:'🥾',swimming:'🏊',football:'⚽',tennis:'🎾',yoga:'🧘',other:'✨'};

function notify(message) {
  const t=$('toast'); t.textContent=message; t.classList.add('show');
  clearTimeout(state.toastTimeout); state.toastTimeout=setTimeout(()=>t.classList.remove('show'),4700);
}
async function request(path,options={}) {
  const {body,...rest}=options;
  const headers={'Accept':'application/json',...rest.headers};
  if(body!==undefined) headers['Content-Type']='application/json';
  if(state.csrf && options.method && !['GET','HEAD'].includes(options.method.toUpperCase())) headers['X-CSRF-Token']=state.csrf;
  const r=await fetch(path,{...rest,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body)});
  let response;
  try{response=await r.json();}catch{response={detail:'Réponse invalide du serveur.'};}
  if(!r.ok){
    if(r.status===401){state.data=null;state.csrf=null;showAuth();}
    let detail=response.detail;
    if(Array.isArray(detail)) detail=detail.map(x=>x.msg).join('; ');
    throw new Error(detail||`Erreur HTTP ${r.status}`);
  }
  return response;
}
function showAuth(){
  $('portal').classList.add('hidden');$('auth').classList.remove('hidden');
}
function showPortal(){
  $('auth').classList.add('hidden');$('portal').classList.remove('hidden');
}
function authMode(mode){
  state.authMode=mode;
  $('auth-login-tab').classList.toggle('selected',mode==='login');
  $('auth-register-tab').classList.toggle('selected',mode==='register');
  $('invite-box').classList.toggle('hidden',mode!=='register');
  $('auth-title').textContent=mode==='login'?'Heureux de te revoir 👋':'Bienvenue chez SportPulse ✨';
  $('auth-desc').textContent=mode==='login'?'Connecte-toi pour consulter ton tableau de bord sportif.':'Crée ton espace personnel avec ton invitation RH.';
  $('auth-submit').textContent=mode==='login'?'Se connecter →':'Créer mon compte →';
  $('auth-password').autocomplete=mode==='login'?'current-password':'new-password';
  $('auth-password').minLength=mode==='login'?1:12;
  $('auth-error').classList.add('hidden');
}
async function authSubmit(e){
  e.preventDefault();
  $('auth-error').classList.add('hidden');
  const btn=$('auth-submit'); btn.disabled=true;
  try{
    const body={email:$('auth-email').value.trim(),password:$('auth-password').value};
    if(state.authMode==='register') body.invite_code=$('auth-invite').value.trim();
    const res=await request('/employee/api/'+(state.authMode==='register'?'register':'login'),{method:'POST',body});
    state.csrf=res.csrf_token;
    $('auth-password').value='';$('auth-invite').value='';
    await loadDashboard();
    notify(state.authMode==='register'?'Compte créé, bienvenue !':'Bienvenue dans ton espace !');
  }catch(err){ $('auth-error').textContent=err.message;$('auth-error').classList.remove('hidden');}
  finally{btn.disabled=false;}
}
function navigate(page){
  const labels={dashboard:"Vue d'ensemble",activities:'Mes activités',commutes:'Mes trajets',benefits:'Mes avantages'};
  if(!labels[page]) return;
  state.page=page;
  for(const p of Object.keys(labels)) $('page-'+p).classList.toggle('hidden',p!==page);
  for(const b of document.querySelectorAll('[data-page]')) b.classList.toggle('active',b.dataset.page===page);
  $('current-page-label').textContent=labels[page];
}
function el(tag,cls,text){
  const node=document.createElement(tag);
  if(cls)node.className=cls;
  if(text!==undefined&&text!==null)node.textContent=String(text);
  return node;
}
function empty(node,message){node.replaceChildren(el('div','empty',message));}
function addActivityRow(container,a){
  const row=el('div','activity-row'),icon=el('span','activity-icon',sports[a.activity_type]||'✨'),desc=el('div','activity-text');
  desc.append(el('strong','',a.label||a.activity_type));
  const detail=[dateLabel(a.started_at),`${a.duration_min} min`,a.validation_status==='simulated'?'Simulé':a.validation_status==='historique_sans_validation_RH'?'Historique simulé':'Déclarée (POC)'].join(' · ');
  desc.append(el('small','',detail));
  if(a.comment)desc.append(el('small','',` · ${a.comment}`));
  const km=el('span','activity-value',a.distance_km==null?'—':`${nf(a.distance_km)} km`);
  row.append(icon,desc,km);
  if(a.editable){
    const buttons=el('div','sportpulse-activity-actions');
    const edit=el('button','btn btn-secondary','Modifier');edit.type='button';
    edit.addEventListener('click',()=>startEdit(a));
    const remove=el('button','btn btn-secondary','Supprimer');remove.type='button';
    remove.addEventListener('click',()=>removeActivity(a));
    buttons.append(edit,remove);row.append(buttons);
  }
  container.append(row);
}

function startEdit(a){
  editingActivity=a;
  $('activity-form-mode').textContent='MODIFIER MA SÉANCE';
  $('activity-type').value=a.activity_type;
  const dt=new Date(a.started_at); const local=new Date(dt.getTime()-dt.getTimezoneOffset()*60000);
  $('activity-date').value=local.toISOString().slice(0,16);
  $('activity-duration').value=a.duration_min;
  $('activity-distance').value=a.distance_km==null?'':a.distance_km;
  $('activity-comment').value=a.comment||'';
  const consent=$('activity-share-slack');if(consent){consent.checked=false;consent.disabled=true;}
  $('activity-dialog').showModal();
}
// Modale native HTML <dialog> : centrée, accessible, sans window.confirm().
// Ne touche PAS à l'appel DELETE, au chargement du dashboard ni à la synchronisation Slack.
function confirmActivityDeletion(activity) {
  const dialog = $('delete-activity-dialog');
  if (!dialog || dialog.open) return Promise.resolve(false);

  // Ne réutilise jamais un ancien choix après une ouverture ou un appui sur Échap.
  dialog.returnValue = 'cancel';
  try {
    dialog.showModal();
  } catch (error) {
    console.error('Impossible d’ouvrir la confirmation de suppression', error);
    return Promise.resolve(false);
  }

  return new Promise(resolve => {
    const onCancel = () => { dialog.returnValue = 'cancel'; };
    const onClose = () => {
      dialog.removeEventListener('cancel', onCancel);
      resolve(dialog.returnValue === 'delete');
    };
    dialog.addEventListener('cancel', onCancel);
    dialog.addEventListener('close', onClose, {once: true});
  });
}

async function removeActivity(a){
 if (!(await confirmActivityDeletion(a))) return;
  try{
    const r=await request('/employee/api/activities/'+encodeURIComponent(a.id)+'?revision='+encodeURIComponent(a.revision||1),{method:'DELETE'});
    await loadDashboard();notify('Activité supprimée. Slack : '+r.slack_sync);
  }catch(e){notify(e.message)}
}

function fillActivities(id,activities,limit){
  const node=$(id);node.replaceChildren();
  if(!activities.length){empty(node,'Aucune activité enregistrée sur cette période.');return;}
  (limit?activities.slice(0,limit):activities).forEach(a=>addActivityRow(node,a));
}
function commuteStatus(label){return label==='validated'?'Validé':label==='rejected'?'Rejeté':'À vérifier';}
function fillCommutes(id,commutes,limit){
  const node=$(id);node.replaceChildren();
  if(!commutes.length){empty(node,'Aucun trajet enregistré sur cette période.');return;}
  (limit?commutes.slice(0,limit):commutes).forEach(c=>{
    const row=el('div','commute-row'),icon=el('span','commute-icon',c.mode==='bicycle'||c.mode==='Vélo'?'🚴':'🚶');
    const desc=el('div','commute-text');desc.append(el('strong','',c.mode[0].toUpperCase()+c.mode.slice(1)));
    desc.append(el('small','',`${dateLabel(c.day+'T12:00:00')} · ${c.distance_km==null?'Distance non archivée':nf(c.distance_km)+' km'}`));
    const pill=el('span','status-pill '+(c.status==='validated'?'validated':c.status==='rejected'?'rejected':''),commuteStatus(c.status));
    row.append(icon,desc,pill);node.append(row);
  });
}
function drawChart(data){
  const holder=$('activity-chart'),labels=$('chart-months');
  holder.replaceChildren();labels.replaceChildren();
  const initial=data.period.start;
  const [year,month]=initial.split('-').map(Number);
  const months=Array.from({length:12},(_,i)=>new Date(Date.UTC(year,month-1+i,1)));
  const counts=months.map(d=>data.activities.filter(a=>a.started_at.slice(0,7)===d.toISOString().slice(0,7)).length);
  const peak=Math.max(...counts,1);
  months.forEach((d,i)=>{
    const col=el('div','bar-col'),bar=el('div','bar'+(counts[i]===0?' is-zero':''));
    bar.style.height=`${Math.max(2,counts[i]/peak*100)}%`;
    bar.title=`${d.toLocaleDateString('fr-FR',{month:'long',year:'numeric'})} : ${counts[i]} activité(s)`;
    col.append(bar);holder.append(col);
    labels.append(el('span','',d.toLocaleDateString('fr-FR',{month:'short'})));
  });
  holder.setAttribute('aria-label',counts.map((c,i)=>`${months[i].toLocaleDateString('fr-FR',{month:'short'})}: ${c}`).join(', '));
}
function render(data){
  state.data=data; showPortal();
  const first=data.employee.name.split(/\s+/)[0];
  $('sidebar-name').textContent=data.employee.name;$('sidebar-id').textContent=data.employee.id;
  $('sidebar-avatar').textContent=first.slice(0,1).toUpperCase();
  $('welcome-title').textContent=`Bonjour ${first} 👋`;
  $('welcome-description').textContent=`Voici ton bilan sportif du ${dateLabel(data.period.start+'T12:00:00')} au ${dateLabel(data.period.end_exclusive+'T12:00:00')} (fin exclue).`;
  $('demo-banner').classList.toggle('hidden',!data.employee.demo);
  $('privacy-notice').textContent=data.notices[0]+' '+data.notices[1];
  $('kpi-activities').textContent=nf(data.sports.count);
  $('kpi-activities-sub').textContent=`Progression : ${Math.min(data.sports.count,15)} / ${data.sports.goal} séances`;
  $('kpi-distance').textContent=nf(data.sports.distance_km)+' km';
  $('kpi-bonus').textContent=money(data.benefits.bonus_estimate_eur);
  $('kpi-days').textContent=`${data.benefits.days_potential ?? 0} potentiels`;
  $('kpi-days-sub').textContent='Estimation POC · sans attribution automatique';
  const goalRatio=Math.min(data.sports.count/data.sports.goal,1);
  $('goal-current').textContent=`${data.sports.count}/${data.sports.goal} séances`;
  $('goal-percent').textContent=Math.round(goalRatio*100)+'%';
  $('goal-bar').style.width=(goalRatio*100)+'%';
  $('score-value').textContent=data.sports.score;
  $('score-ring').style.setProperty('--angle',data.sports.score/100*360+'deg');
  drawChart(data);
  fillActivities('recent-activities',data.activities,4);
  fillActivities('all-activities',data.activities);
  fillCommutes('recent-commutes',data.commutes,4);
  fillCommutes('all-commutes',data.commutes);
  $('activities-count').textContent=`${data.sports.count} séances`;
  $('benefit-bonus').textContent=money(data.benefits.bonus_estimate_eur);
  $('benefit-bonus-state').textContent=data.employee.demo?'Montant fictif — taux initial de 5 %':`Statut RH : ${data.benefits.bonus_status}`;
  $('benefit-days').textContent=`${data.benefits.days_potential ?? 0} jours potentiels`;
  $('benefit-days-state').textContent='Projection POC : 15 activités enregistrées. Aucun droit automatique.';
  $('benefit-meter').style.width=Math.round(goalRatio*100)+'%';
  $('demo-simulator').classList.toggle('hidden',!data.employee.demo);
  if(data.employee.demo){$('bonus-slider').value=5;$('slider-value').textContent='5%';}
  navigate(state.page);
  window.dispatchEvent(new CustomEvent('sportpulse:dashboard',{detail:data}));
}
async function loadDashboard(){
  const year=Number($('year-select').value)||undefined;
  const info=await request('/employee/api/dashboard'+(year?'?year='+year:''));
  const select=$('year-select');
  const selected=select.value;
  select.replaceChildren();
  for(let y=info.year+1;y>=info.year-3;y--){
    const opt=el('option','',String(y));opt.value=String(y);select.append(opt);
  }
  select.value=selected&&[...select.options].some(o=>o.value===selected)?selected:String(info.year);
  render(info);
}
window.loadDashboard=loadDashboard;
async function restore(){
  try{
    await request('/employee/api/session');
    const csrf=await request('/employee/api/csrf',{method:'POST'});
    state.csrf=csrf.csrf_token;
    await loadDashboard();
  }catch{showAuth();}
}
async function addActivity(e){
  e.preventDefault();
  $('activity-error').classList.add('hidden');
  const rawDate=$('activity-date').value;
  const d=new Date(rawDate);
  if(!rawDate||Number.isNaN(d.getTime())){$('activity-error').textContent='Date invalide.';$('activity-error').classList.remove('hidden');return;}
  const btn=$('save-activity');btn.disabled=true;
  try{
    const body={activity_type:$('activity-type').value,started_at:d.toISOString(),duration_min:Number($('activity-duration').value),
      distance_km:$('activity-distance').value===''?null:Number($('activity-distance').value),
      comment:$('activity-comment').value.trim(),
      share_to_slack:$('activity-share-slack').checked};
    const wasEdit=!!editingActivity;
    if(wasEdit){body.revision=editingActivity.revision||1; delete body.share_to_slack;}
    const result=await request(wasEdit?'/employee/api/activities/'+encodeURIComponent(editingActivity.id):'/employee/api/activities',
      {method:wasEdit?'PATCH':'POST',body});
    editingActivity=null; $('activity-form-mode').textContent='NOUVELLE SÉANCE';
    if($('activity-share-slack'))$('activity-share-slack').disabled=false;
    $('activity-dialog').close();
    $('activity-comment').value='';$('activity-distance').value='';$('activity-share-slack').checked=false;
    await loadDashboard();
    notify(wasEdit?'Activité modifiée. Synchronisation Slack : '+result.slack_sync:result.message+(result.slack_status==='queued'?' Publication Slack en cours…':''));
  }catch(err){$('activity-error').textContent=err.message;$('activity-error').classList.remove('hidden');}
  finally{btn.disabled=false;}
}
window.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-auth]').forEach(b=>b.addEventListener('click',()=>authMode(b.dataset.auth)));
  $('auth-form').addEventListener('submit',authSubmit);
  document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.page)));
  document.querySelectorAll('[data-goto]').forEach(b=>b.addEventListener('click',()=>navigate(b.dataset.goto)));
  $('year-select').addEventListener('change',()=>loadDashboard().catch(err=>notify(err.message)));
  $('new-activity').addEventListener('click',()=>{
    editingActivity=null; $('activity-form-mode').textContent='NOUVELLE SÉANCE';
    if($('activity-share-slack')){$('activity-share-slack').disabled=false;$('activity-share-slack').checked=false;}
    const today=new Date(); const local=new Date(today.getTime()-today.getTimezoneOffset()*60000);
    $('activity-date').value=local.toISOString().slice(0,16);
    $('activity-dialog').showModal();
  });
  $('close-dialog').addEventListener('click',()=>$('activity-dialog').close());
  $('cancel-activity').addEventListener('click',()=>$('activity-dialog').close());
  $('activity-form').addEventListener('submit',addActivity);
  $('bonus-slider').addEventListener('input',e=>{
    const rate=Number(e.target.value);
    $('slider-value').textContent=rate+'%';
    $('benefit-bonus').textContent=money(42000*rate/100);
    $('benefit-bonus-state').textContent='Scénario fictif ; la règle de paie ne change pas';
  });
  $('logout').addEventListener('click',async()=>{
    try{await request('/employee/api/logout',{method:'POST'});}catch{}
    state.csrf=null;state.data=null;showAuth();authMode('login');notify('Vous êtes déconnecté.');
  });
  restore();
});
