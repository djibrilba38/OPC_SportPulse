/* SportPulse 15 : une vue salariée sans QR/code, avec carte et activités.
   Toutes les modifications d'activité restent gérées par app.js et ses API. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  window.addEventListener('DOMContentLoaded', () => {
    const dash = $('page-dashboard');
    const history = $('page-activities');
    if (!dash) return;
    // L'historique reste accessible depuis la vue unique sans dupliquer les activités.
    if (history && !$('sp15-history')) {
      const details = document.createElement('details');
      details.id = 'sp15-history';
      details.className = 'sp15-history';
      const summary = document.createElement('summary');
      summary.textContent = 'Toutes mes activités · modifier ou supprimer';
      details.append(summary, history);
      history.classList.remove('hidden');
      dash.append(details);
      document.querySelectorAll('[data-goto="activities"], [data-page="activities"]').forEach(button => {
        button.addEventListener('click', event => {
          event.preventDefault(); event.stopImmediatePropagation();
          const dashTab = document.querySelector('[data-page="dashboard"]');
          if (dashTab) dashTab.click();
          history.classList.remove('hidden');
          details.open = true;
          details.scrollIntoView({behavior:'smooth',block:'start'});
        }, true);
      });
    }
    // app.js réinitialise les pages après chaque chargement : conserver l'historique visible.
    window.addEventListener('sportpulse:dashboard', () => {
      if ($('sp15-history')) history?.classList.remove('hidden');
    });
    // Le navigateur n'affiche plus aucune validation par QR ni code temporaire.
    const map = $('sp12-map-panel');
    if (map) {
      const chart = dash.querySelector('.dashboard-grid');
      const bottom = dash.querySelector('.bottom-grid');
      (chart || bottom)?.before(map);
    }
  });
})();
