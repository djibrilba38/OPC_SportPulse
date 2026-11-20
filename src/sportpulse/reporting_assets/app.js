'use strict';
/* Reporting 16: source PostgreSQL Gold obligatoire, aucune option DEMO ni lien replay. */
const $ = id => document.getElementById(id);
const N = v => Number(v||0).toLocaleString('fr-FR',{maximumFractionDigits:2});
const E = v => v==null?'—':Number(v).toLocaleString('fr-FR',{style:'currency',currency:'EUR'});
const MONTHS=['jan.','fév.','mars','avr.','mai','juin','juil.','août','sept.','oct.','nov.','déc.'];
let refreshing=false;
function element(tag,cl,value){const e=document.createElement(tag);if(cl)e.className=cl;if(value!=null)e.textContent=String(value);return e;}
function renderMonths(report){
  const byMonth=Object.fromEntries(report.monthly.map(m=>[m.month,m.activities]));
  const start=new Date(`${report.period.start}T12:00:00Z`);
  const dates=Array.from({length:12},(_,i)=>new Date(Date.UTC(start.getUTCFullYear(),start.getUTCMonth()+i,1)));
  const max=Math.max(1,...Object.values(byMonth));
  const box=$('months');box.replaceChildren();
  dates.forEach(d=>{
    const key=d.toISOString().slice(0,7),count=byMonth[key]||0;
    const card=element('div','month-item');card.append(element('span','month-value',count));
    const bar=element('div','month-bar');bar.style.height=`${Math.max(2,count/max*122)}px`;
    bar.setAttribute('aria-label',`${MONTHS[d.getUTCMonth()]} : ${count} activités`);
    card.append(bar,element('span','month-label',MONTHS[d.getUTCMonth()]));box.append(card);
  });
}
function renderSports(items){
  const box=$('sports');box.replaceChildren();
  if(!items.length){box.textContent='Aucune activité sur la période';return;}
  const max=Math.max(1,...items.map(x=>x.count));
  for(const sport of items.slice(0,8)){
    const row=element('div','list-row');row.append(element('span',null,sport.sport));
    const bg=element('div','bar-bg'),bar=element('div','bar');
    bar.style.width=`${100*sport.count/max}%`;bg.append(bar);row.append(bg,element('b',null,sport.count));box.append(row);
  }
}
function renderLatest(list){
  const body=$('latest');body.replaceChildren();
  if(!list.length){const tr=element('tr');const td=element('td',null,'Aucune activité');td.colSpan=5;tr.append(td);body.append(tr);return;}
  for(const activity of list.slice(0,5)){
    const tr=element('tr');
    const origin=activity.validation==='simulation_synthetique'?'Historique synthétique':'Déclaration du portail';
    for(const value of [activity.date,activity.sport,activity.distance_km==null?'—':N(activity.distance_km)+' km',activity.duration_min+' min',origin]){
      tr.append(element('td',null,value));
    }
    body.append(tr);
  }
}
function render(report){
  $('report').hidden=false;
  $('period').textContent=`${report.source_label} · ${report.period.start} → ${report.period.end_exclusive} (fin exclue)`;
  $('employees').textContent=N(report.employee_count_reference);
  $('activities').textContent=N(report.activity_count);
  $('activity-source-detail').textContent=`${N(report.pipeline.imported_synthetic_activities)} synthétiques Gold + ${N(report.pipeline.portal_declarations)} déclaration(s) portail`;
  $('distance').textContent=N(report.distance_km)+' km';
  $('duration').textContent=N(report.duration_min)+' min';
  $('days').textContent=N(report.wellbeing.potential_days)+' jours';
  $('eligible').textContent=N(report.wellbeing.potential_eligible_employees);
  $('days-cost').textContent=E(report.wellbeing.estimated_capacity_cost_eur);
  const f=report.finance||{};
  $('bonus').textContent=E(f.candidate_budget_eur);
  $('bonus-candidates').textContent=f.candidates==null?'—':N(f.candidates);
  $('bonus-validated').textContent=E(f.validated_budget_eur);
  $('bonus-rule').textContent=f.rate_percent==null?'—':`${N(f.rate_percent)} % · ${f.rules_version||'—'}`;
  renderMonths(report);renderSports(report.activity_types);renderLatest(report.latest_activities);
  $('notes').replaceChildren(...(report.notes||[]).map(note=>element('li',null,note)));
}
async function refresh(quiet=false){
  if(refreshing)return;
  refreshing=true;
  try{
    const year=Number($('year').value);
    if(!Number.isInteger(year)||year<2020||year>2100)throw Error('Année sociale invalide.');
    const response=await fetch(`/admin/api/reporting/annual?source=postgres&year=${encodeURIComponent(year)}`,{credentials:'same-origin',cache:'no-store'});
    if(response.status===401)throw Error('Session RH expirée : reconnecte-toi depuis /admin/rh.');
    const data=await response.json();
    if(!response.ok)throw Error(data.detail||`Erreur HTTP ${response.status}`);
    render(data);
    $('notice').classList.remove('error');
    $('notice').textContent=`Source active : PostgreSQL Gold + RH · Actualisé à ${new Date().toLocaleTimeString('fr-FR')}`;
  }catch(err){$('notice').classList.add('error');$('notice').textContent=err.message;if(!quiet)$('report').hidden=true;}
  finally{refreshing=false;}
}
$('refresh').addEventListener('click',()=>refresh());
$('year').addEventListener('change',()=>refresh());
refresh();
setInterval(()=>{if(!document.hidden)refresh(true);},15000);
