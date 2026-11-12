/* SportPulse étape 16 — avantages personnels, sans salaire inventé, et objectif du mois.
   Ne modifie ni la gestion des activités ni Slack. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const money = n => Number(n).toLocaleString('fr-FR', {style:'currency', currency:'EUR'});
  let lastYear = null;
  let currentRequest = 0;
  async function update(year) {
    const root = $('sp16-benefits');
    if (!root) return;
    const request = ++currentRequest;
    let url = '/employee/api/benefits-overview';
    if (Number.isInteger(year)) url += '?year=' + encodeURIComponent(String(year));
    try {
      const response = await fetch(url, {credentials: 'same-origin',cache:'no-store'});
      if (response.status === 401) {root.hidden = true; return;}
      if (!response.ok) {throw new Error(`Données personnelles indisponibles (HTTP ${response.status}).`);}
      const d = await response.json();
      if (request !== currentRequest) return;
      root.hidden = false;
      $('sp16-warning').hidden = true;
      const month = d.monthly || {month:'—',count:0,goal:4,progress_percent:0,remaining:4};
      $('sp16-month-name').textContent = month.month;
      $('sp16-month-total').textContent = `${month.count} / ${month.goal}`;
      $('sp16-month-progress').style.width = `${Math.max(0,Math.min(100,month.progress_percent))}%`;
      $('sp16-month-note').textContent = month.remaining === 0 ? 'Objectif mensuel atteint !' : `Encore ${month.remaining} séance(s) pour ton objectif du mois`;
      $('sp16-year-total').textContent = `${d.annual_activities} / ${d.annual_goal}`;
      $('sp16-days').textContent = `${d.wellbeing.potential_days} jour(s) potentiels`;
      const f = d.finance || {};
      const bonus = $('sp16-bonus');
      const note = $('sp16-bonus-note');
      if (d.is_demo) {
        bonus.textContent = 'Non applicable';
        note.textContent = 'Compte de démonstration : aucun salaire RH associé.';
      } else if (!f.linked_rh) {
        bonus.textContent = 'À rattacher';
        note.textContent = 'Ton compte doit être relié à ton identifiant RH par l’administration.';
      } else if (f.budget_potential_eur !== null) {
        bonus.textContent = money(f.budget_potential_eur);
        note.textContent = `Estimation RH à ${f.rate_percent}% · trajet à contrôler · aucune prime versée confirmée.`;
      } else if (f.candidate === false) {
        bonus.textContent = 'Non candidate';
        note.textContent = 'Selon le mode de transport déclaré, sous réserve d’une révision RH.';
      } else {
        bonus.textContent = 'À confirmer';
        note.textContent = 'Aucun scénario financier applicable à ce compte.';
      }
      $('sp16-legal').textContent = 'Les activités du POC peuvent être synthétiques. Le seuil de 15 séances et les montants affichés ne constituent pas une attribution RH ni un paiement.';
    } catch (err) {
      if (request !== currentRequest) return;
      root.hidden = false;
      $('sp16-warning').hidden = false;
      $('sp16-warning').textContent = err.message;
    }
  }
  window.addEventListener('sportpulse:dashboard', event => {
    const year = Number(event.detail?.year);
    if (Number.isInteger(year)) lastYear = year;
    update(lastYear);
  });
  document.addEventListener('DOMContentLoaded', () => update(lastYear));
})();
