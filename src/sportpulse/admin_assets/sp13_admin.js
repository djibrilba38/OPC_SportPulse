/* SportPulse 13 : démos QR choisies sans taper les ID et confirmation d'archivage. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  let profiles=[];
  function message(text, error=false){const el=$('message');if(el){el.textContent=text;el.classList.remove('hidden');el.classList.toggle('error',error);}}
  async function loadProfiles(){
    try{
      const response=await fetch('/admin/api/demo-profiles',{credentials:'same-origin'});
      if(response.status===401 || response.status===403)return;
      const data=await response.json();if(!response.ok)throw Error(data.detail||'Chargement impossible');
      profiles=data.profiles||[];
      const choice=$('sp13-profile');const item=$('sp13-item');
      if(!choice||!item)return;
      choice.replaceChildren();
      const empty=document.createElement('option');empty.value='';empty.textContent=profiles.length?'Choisir un salarié fictif…':'Aucun compte fictif disponible';choice.append(empty);
      profiles.forEach(p=>{const opt=document.createElement('option');opt.value=p.employee_id;opt.textContent=p.name+' · '+p.employee_id;choice.append(opt);});
      item.replaceChildren();const hint=document.createElement('option');hint.textContent='Choisir un salarié d’abord';hint.value='';item.append(hint);
      $('sp13-sample-help').textContent=profiles.length?'Choisis un salarié et une activité en attente : les champs RH se remplissent automatiquement.':'Pour créer trois comptes fictifs : docker compose exec app python -m sportpulse.seed_demo_employees --create';
    }catch(err){if($('sp13-sample-help'))$('sp13-sample-help').textContent='Échantillons indisponibles : '+err.message;}
  }
  function selectedProfile(){return profiles.find(x=>x.employee_id===$('sp13-profile')?.value);}
  function updateItems(){
    const current=selectedProfile(), list=$('sp13-item');if(!list)return;
    list.replaceChildren();const empty=document.createElement('option');empty.value='';empty.textContent=current?'Choisir une activité ou un trajet…':'Choisir un salarié…';list.append(empty);
    (current?.items||[]).forEach(x=>{const option=document.createElement('option');option.value=x.ref;option.textContent=x.label;option.dataset.kind=x.kind;list.append(option);});
    if(current)$('sp12-employee').value=current.employee_id;
  }
  function applyRef(){const select=$('sp13-item');const ref=select?.value;if(!ref)return;
    $('sp12-activity').value=ref;
    $('sp12-kind').value=select.selectedOptions[0].dataset.kind;
  }
  function confirmArchive(title){return new Promise(resolve=>{
    const dialog=$('sp13-confirm');if(!dialog || typeof dialog.showModal!=='function'){resolve(window.confirm(title));return;}
    $('sp13-confirm-title').textContent=title;
    dialog.returnValue='cancel';dialog.showModal();
    dialog.addEventListener('close',()=>resolve(dialog.returnValue==='archive'),{once:true});
  });}
  async function archiveRow(kind, ref){
    const ok=await confirmArchive(kind==='simulation'?'Retirer cette simulation de l’historique affiché ?':'Retirer cette ligne du journal affiché ?');
    if(!ok)return;
    try{
      // Le backend conserve l'audit original et valide rôle + CSRF.
      const csrfResponse=await fetch('/admin/api/csrf',{method:'POST',credentials:'same-origin'});
      const token=await csrfResponse.json();if(!csrfResponse.ok)throw Error(token.detail||'Session expirée');
      const url=kind==='simulation'?'/admin/api/history/'+encodeURIComponent(ref):'/admin/api/audit/'+encodeURIComponent(ref);
      const response=await fetch(url,{method:'DELETE',credentials:'same-origin',headers:{'X-CSRF-Token':token.csrf_token}});
      const data=await response.json();if(!response.ok)throw Error(data.detail||'Archivage impossible');
      // Les fonctions de l'admin originel rafraîchissent la liste (MAX 5 côté serveur).
      $(kind==='simulation'?'history-refresh':'audit-refresh')?.click();
      message('Entrée retirée de l’affichage. La trace d’audit reste conservée.');
    }catch(err){message(err.message,true);}
  }
  window.addEventListener('DOMContentLoaded',()=>{
    const form=$('sp12-code-form');
    if(form){
      const box=document.createElement('div');box.className='sp13-samples';
      const title=document.createElement('strong');title.textContent='Tester un compte salarié fictif';
      const actions=document.createElement('div');actions.className='sp13-sample-fields';
      const people=document.createElement('select');people.id='sp13-profile';people.setAttribute('aria-label','Salarié fictif');
      const items=document.createElement('select');items.id='sp13-item';items.setAttribute('aria-label','Activité ou trajet de test');
      const refresh=document.createElement('button');refresh.textContent='Actualiser';refresh.type='button';refresh.className='outline';refresh.addEventListener('click',loadProfiles);
      actions.append(people,items,refresh);
      const help=document.createElement('p');help.id='sp13-sample-help';help.className='muted';help.textContent='Chargement des échantillons…';
      box.append(title,actions,help);form.before(box);
      people.addEventListener('change',updateItems);items.addEventListener('change',applyRef);
    }
    // Un seul listener pour les boutons ajoutés lors des rechargements du tableau.
    document.addEventListener('click',event=>{
      const button=event.target.closest('[data-sp13-archive]');if(!button)return;
      const kind=button.dataset.sp13Archive;
      archiveRow(kind,button.dataset.sp13Ref);
    });
    const modal=document.createElement('dialog');modal.id='sp13-confirm';modal.className='sp13-confirm';
    const formModal=document.createElement('form');formModal.method='dialog';
    const title=document.createElement('h3');title.id='sp13-confirm-title';title.textContent='Retirer cette entrée ?';
    const desc=document.createElement('p');desc.textContent='L’entrée disparaît du tableau. Les données sources et la piste d’audit sont conservées.';
    const actions=document.createElement('div');actions.className='sp13-confirm-actions';
    const cancel=document.createElement('button');cancel.value='cancel';cancel.textContent='Annuler';cancel.className='outline';
    const approve=document.createElement('button');approve.value='archive';approve.textContent='Retirer';approve.className='primary';
    actions.append(cancel,approve);formModal.append(title,desc,actions);modal.append(formModal);document.body.append(modal);
    const dash=$('dashboard');
    if(dash){
      const watch=new MutationObserver(()=>{
        if(!dash.classList.contains('hidden')){loadProfiles();watch.disconnect();}
      });watch.observe(dash,{attributes:true,attributeFilter:['class']});
      if(!dash.classList.contains('hidden'))loadProfiles();
    }
  });
})();
