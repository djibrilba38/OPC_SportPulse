/* Regroupe les outils utiles dans une seule vue, sans supprimer les fonctions métier. */
(() => {
  'use strict';
  const $=id=>document.getElementById(id);
  window.addEventListener('DOMContentLoaded',()=>{
    const dash=$('page-dashboard');if(!dash || $('sp13-validation-toggle'))return;
    const kpis=dash.querySelector('.kpi-grid');
    const proof=$('sp12-proof-overview');
    const verify=$('sp12-validation-panel');
    const map=$('sp12-map-panel');
    // D'abord les indicateurs, puis la carte et les dernières activités.
    // Le compteur détaillé de validation et le formulaire sont accessibles en un clic.
    if(proof && verify){
      const details=document.createElement('details');details.id='sp13-validation-toggle';
      details.className='sp13-verify-details';
      const summary=document.createElement('summary');summary.innerHTML='<span>✓ Valider une activité</span><span class="sp13-small">Scanner QR ou saisir un code RH</span>';
      details.append(summary,proof,verify);
      kpis?.after(details);
    }
    if(map){const details=$('sp13-validation-toggle');(details||kpis)?.after(map);}
    const oldGrid=dash.querySelector('.dashboard-grid');
    if(oldGrid)oldGrid.classList.add('sp13-deemphasized');
    const bottom=dash.querySelector('.bottom-grid');
    if(bottom){
      bottom.classList.add('sp13-single-column');
      const duplicated=bottom.querySelector('#recent-commutes')?.closest('.panel');
      if(duplicated)duplicated.classList.add('sp13-duplicate-commutes');
    }
    // Les données de la section d'historique restent disponibles dans le volet existant.
    const notice=$('privacy-notice');if(notice){notice.classList.add('sp13-compact-notice');}
    // Dans le portail, toute validation reste liée à un élément précis.
    const ref=$('sp12-validation-ref');if(ref)ref.placeholder='Sélectionne une activité ci-dessous';
    const selector=document.createElement('button');selector.type='button';selector.className='btn btn-secondary sp13-show-validation';
    selector.textContent='Valider avec QR / code';
    selector.addEventListener('click',()=>{const d=$('sp13-validation-toggle');d.open=true;d.scrollIntoView({behavior:'smooth',block:'center'});ref?.focus();});
    const header=dash.querySelector('.kpi-grid');if(header)header.before(selector);
    // Redirection propre des boutons d'historique vers les sections repliables.
    document.querySelectorAll('[data-goto="activities"],[data-goto="commutes"]').forEach(button=>{
      button.addEventListener('click',event=>{
        const name=button.dataset.goto;
        const details=document.querySelector(`#sp12-all-details details[data-inline-page="${name}"]`);
        if(details){event.preventDefault();event.stopPropagation();details.open=true;details.scrollIntoView({behavior:'smooth',block:'start'});}
      },true);
    });
  });
})();
