/* SportPulse — Étape 12 : une seule vue, validation par QR ou code, carte sans tracé fictif. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  let latest = null, map = null, line = null, points = [], markers = [], camera = null, scanning = false;
  const current = { ref: '' };

  const info = msg => { if ($('sp12-message')) $('sp12-message').textContent = msg; };
  const esc = s => String(s ?? '');
  async function api(url, opts = {}) {
    const csrf = opts.method ? (await (await fetch('/employee/api/csrf', {method:'POST',credentials:'same-origin'})).json()).csrf_token : '';
    const response = await fetch(url, {credentials:'same-origin', ...opts,
      headers: {'Accept':'application/json',...(opts.method?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{})}});
    let payload={}; try {payload=await response.json();}catch{}
    if (!response.ok) throw Error(payload.detail || `HTTP ${response.status}`);
    return payload;
  }
  function refForActivity(a) {
    const raw = String(a.id || '');
    if (raw.startsWith('demo-') || raw.startsWith('decl-')) return raw;
    return latest?.employee?.demo ? `demo-${raw}` : '';
  }
  function statusName(s) {
    return s==='validated' ? 'Validée' : s==='rejected' ? 'Rejetée' :
           s==='historique_sans_validation_RH' ? 'Historique non vérifié' : 'En attente du code RH';
  }
  function addProofAction(node, ref, kind, eligible) {
    if (!node || !eligible || !ref || node.querySelector('.sp12-proof-btn')) return;
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'btn btn-secondary sp12-proof-btn'; b.textContent = '✓ Valider par code';
    b.addEventListener('click', () => {
      current.ref = ref;
      $('sp12-validation-ref').value = ref;
      $('sp12-validation-code').focus();
      $('sp12-validation-panel').scrollIntoView({behavior:'smooth',block:'center'});
      info((kind==='commute'?'Trajet':'Activité')+' sélectionné : '+ref+'. Saisis le code communiqué par RH.');
    });
    node.append(b);
  }
  function renderProofs(data) {
    const activities = data.activities || [];
    const count = data.sports || {};
    $('sp12-validated-count').textContent = String(count.count || 0);
    $('sp12-pending-count').textContent = String(count.pending_count ?? 0);
    $('sp12-total-count').textContent = String(count.declared_count ?? activities.length);
    const label = $('sp12-activity-label');
    label.textContent = 'Activités vérifiées (non vérifiées : '+(count.pending_count ?? 0)+')';
    const bindList = (id, slice) => {
      const rows = Array.from($(id)?.querySelectorAll('.activity-row') || []);
      slice.forEach((a,i) => {
        const row = rows[i]; if (!row) return;
        row.querySelector('.sp12-status')?.remove();
        const badge=document.createElement('span');badge.className='sp12-status '+(a.counted_for_benefits?'ok':'waiting');
        badge.textContent=statusName(a.validation_status);
        row.append(badge);
        if (a.validation_status==='pending' && refForActivity(a)) {
          const reference=document.createElement('code');
          reference.className='sp12-ref';
          reference.textContent=refForActivity(a);
          row.append(reference);
        }
        addProofAction(row, refForActivity(a), 'activity',
          !a.counted_for_benefits && a.validation_status==='pending');
      });
    };
    bindList('recent-activities', activities.slice(0,4));
    bindList('all-activities', activities);
    const commutes = data.commutes || [];
    for (const [id, rows] of [['recent-commutes',commutes.slice(0,4)],['all-commutes',commutes]]) {
      const items = Array.from($(id)?.querySelectorAll('.commute-row') || []);
      rows.forEach((trip,i) => addProofAction(items[i], trip.proof_ref, 'commute', trip.status !== 'validated' && trip.status !== 'rejected'));
    }
    showTrips(commutes);
  }
  function showTrips(commutes) {
    const parent=$('sp12-trip-items');parent.replaceChildren();
    if(!commutes.length){parent.textContent='Aucun trajet déclaré sur la période.';return;}
    commutes.slice(0,5).forEach(c => {
      const b=document.createElement('button');b.type='button';b.className='sp12-trip-item';
      const validation=c.arrival_code_verified && c.status!=='validated' ? 'Arrivée confirmée · RH en attente' : statusName(c.status);
      b.textContent=`${c.day} · ${c.mode} · ${c.distance_km ?? '–'} km · ${validation} · ${c.proof_ref}`;
      b.addEventListener('click',() => {
        $('sp12-map-caption').textContent=`Trajet du ${c.day} · ${c.mode} · ${c.status}. Aucun tracé GPS historique enregistré : les points de la carte sont indicatifs.`;
        parent.querySelectorAll('button').forEach(x=>x.classList.remove('active'));
        b.classList.add('active');
      });parent.append(b);
    });
  }
  function unifyNavigation() {
    const dash=$('page-dashboard'); if(!dash || $('sp12-all-details')) return;
    const wrapper=document.createElement('div');wrapper.id='sp12-all-details';wrapper.className='sp12-details';
    for (const [page,title] of [['activities','Historique des activités'],['commutes','Tous les trajets'],['benefits','Mes avantages']]) {
      const section=$('page-'+page);if(!section) continue;
      const d=document.createElement('details');d.dataset.inlinePage=page;
      const summary=document.createElement('summary');summary.textContent=title;
      d.append(summary,section);section.classList.remove('hidden');wrapper.append(d);
    }
    dash.append(wrapper);
    document.querySelectorAll('.side-nav [data-page]:not([data-page="dashboard"])').forEach(b=>b.remove());
    document.addEventListener('click', e => {
      const b=e.target.closest('[data-goto]');if(!b) return;
      const target=wrapper.querySelector(`details[data-inline-page="${b.dataset.goto}"]`);
      if(target){e.preventDefault();e.stopImmediatePropagation();target.open=true;target.scrollIntoView({behavior:'smooth',block:'start'});}
    },true);
  }
  function keepDetailsVisible(){
    document.querySelectorAll('#sp12-all-details .page-panel').forEach(x=>x.classList.remove('hidden'));
  }
  async function createTrip(e){
    e.preventDefault();
    const distance=$('sp12-trip-distance').value;
    try{
      const result=await api('/employee/api/trips', {method:'POST',body:JSON.stringify({
        day:$('sp12-trip-date').value,transport_mode:$('sp12-trip-mode').value,
        distance_km:distance===''?null:Number(distance)})});
      info(result.message+' Référence : '+result.ref);
      current.ref=result.ref;
      $('sp12-validation-ref').value=result.ref;
      if(typeof window.loadDashboard==='function')await window.loadDashboard();
    }catch(err){info('Erreur : '+err.message);}
  }
  async function sendProof(e){
    e.preventDefault();
    const code=$('sp12-validation-code').value.trim();
    // Étape 14 : un QR/code identifie lui-même la preuve liée à ce compte.
    try {
      const result=await api('/employee/api/rh/validate-code', {method:'POST',body:JSON.stringify({code})});
      info(result.notice || 'Validation enregistrée.');
      $('sp12-validation-code').value='';
      if(typeof window.loadDashboard==='function') await window.loadDashboard();
    } catch(err){info('Erreur : '+err.message);}
  }
  async function stopCamera(){
    scanning=false;
    if(camera){camera.getTracks().forEach(t=>t.stop());camera=null;}
    $('sp12-qr-video').hidden=true;
  }
  async function scanCode(){
    if (!('BarcodeDetector' in window) || !navigator.mediaDevices?.getUserMedia){info('Scan non disponible dans ce navigateur. Saisis le code à 8 chiffres.');return;}
    try{
      const detector=new BarcodeDetector({formats:['qr_code']});
      camera=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'},audio:false});
      const video=$('sp12-qr-video');video.srcObject=camera;video.hidden=false;await video.play();scanning=true;
      while(scanning){
        const found=await detector.detect(video);
        if(found.length){$('sp12-validation-code').value=found[0].rawValue.replace(/^sportpulse:/,'');await stopCamera();info('QR détecté. Clique sur « Confirmer la validation ».');break;}
        await new Promise(r=>setTimeout(r,450));
      }
    }catch(err){info('Caméra indisponible : '+err.message);await stopCamera();}
  }
  async function initMap(){
    if(typeof L==='undefined'){$('sp12-map-caption').textContent='Carte non disponible (Leaflet inaccessible). Les trajets restent consultables.';return;}
    let center=[48.8566,2.3522];
    try{
      const r=await api('/employee/api/map-context');
      if(r.office) center=[r.office.lat,r.office.lon];
      else $('sp12-map-caption').textContent='Carte d’exemple (Paris) : l’adresse du bureau n’est pas configurée. Clique pour placer un départ et une arrivée fictifs.';
    }catch{}
    map=L.map('sp12-map',{scrollWheelZoom:false}).setView(center,12);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,attribution:'© OpenStreetMap contributors'}).addTo(map);
    map.on('click',e=>{
      if(points.length===2) resetMap();
      points.push([e.latlng.lat,e.latlng.lng]);
      markers.push(L.marker(e.latlng).addTo(map));
      if(points.length===2) calculateRoute();
      else $('sp12-map-caption').textContent='Sélectionne maintenant l’arrivée sur la carte.';
    });
    setTimeout(()=>map.invalidateSize(),120);
  }
  function resetMap(){
    points=[];markers.forEach(m=>map.removeLayer(m));markers=[];
    if(line)map.removeLayer(line);line=null;
    $('sp12-map-caption').textContent='Clique sur deux points pour afficher un trajet estimé. Les historiques ne disposent pas de tracé GPS.';
  }
  async function calculateRoute(){
    const [origin,dest]=points, qs=new URLSearchParams({origin_lat:origin[0],origin_lon:origin[1],
      destination_lat:dest[0],destination_lon:dest[1],mode:$('sp12-map-mode').value});
    try{
      const result=await api('/geo/route?'+qs.toString());
      if(result.geometry){line=L.geoJSON(result.geometry,{style:{color:'#0a8294',weight:5}}).addTo(map);
        map.fitBounds(line.getBounds().pad(0.15));}
      $('sp12-map-caption').textContent=`Itinéraire indicatif : ${result.distance_km} km, ${result.duration_min} min (${result.mode_label}). Cela ne valide aucun trajet historique.`;
    }catch(err){$('sp12-map-caption').textContent='Itinéraire indisponible : '+err.message;}
  }
  window.addEventListener('DOMContentLoaded',()=>{
    unifyNavigation();
    $('sp12-validation-form')?.addEventListener('submit',sendProof);
    $('sp12-trip-form')?.addEventListener('submit',createTrip);
    $('sp12-trip-date').value=new Date().toLocaleDateString('en-CA');
    $('sp12-scan')?.addEventListener('click',scanCode);
    $('sp12-stop')?.addEventListener('click',stopCamera);
    $('sp12-map-reset')?.addEventListener('click',resetMap);
    window.addEventListener('sportpulse:dashboard',e=>{latest=e.detail;keepDetailsVisible();renderProofs(latest);});
    const portal = $('portal');if(portal){
      const observer=new MutationObserver(()=>{if(!portal.classList.contains('hidden') && map) setTimeout(()=>map.invalidateSize(),100);});
      observer.observe(portal,{attributes:true,attributeFilter:['class']});
    }
    // Carte des trajets retirée du portail salarié à la demande du métier.
  });
})();
