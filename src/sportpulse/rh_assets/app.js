'use strict';
const $ = id => document.getElementById(id);
const state = {page:0, total:0, rowsPerPage:30};
const euro = value => Number(value || 0).toLocaleString('fr-FR',{style:'currency',currency:'EUR',maximumFractionDigits:2});
const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function getJSON(url){const resp=await fetch(url,{credentials:'same-origin',cache:'no-store'});if(resp.status===401){location.href='/admin';throw Error('Connexion administrateur requise');}if(!resp.ok){let reason='Erreur HTTP '+resp.status;try{reason=(await resp.json()).detail||reason;}catch{}throw Error(reason);}return resp.json();}
function setMetric(label, value, note='',primary=false){return `<article class="tile${primary?' primary':''}"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;}
function makeCards(m){$('tiles').innerHTML =
  setMetric('Salariés du fichier RH',m.employees_total,'Effectif importé')+
  setMetric('Masse salariale brute',euro(m.annual_payroll_gross_eur),'Données sources RH')+
  setMetric('Candidats selon mode déclaré',m.employees_declaring_sport_commute,'Trajet à valider')+
  setMetric('Budget provisoire de prime',euro(m.bonus_budget_provisional_eur),m.bonus_rate_percent+' % sur candidats',true)+
  setMetric('Salaires validés RH',m.salary_records_validated_by_hr,'Sur '+m.employees_total+' salariés')+
  setMetric('Avec trajets validés',m.employees_with_validated_commutes,'Contrôle d’arrivée')+
  setMetric('Activités hors travail déclarées (POC)',m.validated_outside_work_activities,'Données PostgreSQL, pas la simulation')+
  setMetric('Journées bien-être potentielles',m.wellbeing_days_potential_pending_rh,'15 activités validées / 5 jours');}
async function refresh(){const year=Number($('year').value),rate=Number($('rate').value);if(!Number.isInteger(year)||year<2020||year>2100||rate<0||rate>20){$('warning').hidden=false;$('warning').textContent='Vérifie l’année et le taux (0 à 20 %).';return;}
 try{const data=await getJSON(`/admin/api/rh/overview?year=${year}&rate_percent=${rate}`);$('source-tag').textContent=data.status==='ok'?'Source RH importée':'Import manquant';if(data.status!=='ok'){$('warning').hidden=false;$('warning').textContent=data.message;$('tiles').replaceChildren();$('departments').replaceChildren();$('employees').replaceChildren();return;}
 $('warning').hidden=true;$('stamp').textContent='Version '+data.import_id+' · '+new Date(data.imported_at).toLocaleString('fr-FR');makeCards(data.metrics);
 $('departments').innerHTML=data.by_department.map(d=>`<tr><td>${esc(d.department)}</td><td>${d.employees}</td><td>${d.sporting_commute_declarations}</td><td>${euro(d.payroll_gross_eur)}</td><td>${euro(d.provisional_bonus_budget_eur)}</td></tr>`).join('');
 $('notes').innerHTML=data.notes.map(n=>'<li>'+esc(n)+'</li>').join(''); await refreshEmployees();
 }catch(err){$('warning').hidden=false;$('warning').textContent=err.message;}}
async function refreshEmployees(){const year=Number($('year').value);try{const data=await getJSON(`/admin/api/rh/employees?year=${year}&limit=${state.rowsPerPage}&offset=${state.page*state.rowsPerPage}`);state.total=data.total;$('employees').innerHTML=data.items.map(r=>`<tr><td>${esc(r.employee_id)}</td><td>${esc(r.department)}</td><td>${euro(r.annual_gross_salary_eur)}</td><td>${esc(r.declared_commute_mode)}</td><td>${r.sporting_commute_candidate?'À vérifier':'Non / à revoir'}</td><td>${r.validated_commutes}</td><td>${r.validated_activities}</td></tr>`).join('')||'<tr><td colspan="7">Aucun salarié</td></tr>';
 $('paging').textContent=`${state.total?state.page*state.rowsPerPage+1:0}–${Math.min(state.total,(state.page+1)*state.rowsPerPage)} / ${state.total}`;$('prev').disabled=state.page<=0;$('next').disabled=(state.page+1)*state.rowsPerPage>=state.total;
 }catch(err){$('employees').innerHTML=`<tr><td colspan="7">${esc(err.message)}</td></tr>`;}}
$('reload').addEventListener('click',()=>{state.page=0;refresh();});$('next').addEventListener('click',()=>{state.page++;refreshEmployees();});$('prev').addEventListener('click',()=>{state.page=Math.max(0,state.page-1);refreshEmployees();});refresh();
