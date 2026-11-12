/* SportPulse 15 : carte OSM autonome et sans QR si la bibliothèque Leaflet externe ne charge pas.
 * Aucun tracé GPS historique reconstruit. Les tuiles nécessitent Internet.
 * Aucune clé API, aucun localStorage. API /geo/route uniquement pour la distance.
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const TILE = 256;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const n = z => Math.pow(2, z);
  function project(lat, lon, z) {
    const sin = Math.sin(clamp(lat, -85.0511, 85.0511)*Math.PI/180);
    return {x:(lon+180)/360*n(z)*TILE,
      y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*n(z)*TILE};
  }
  function invert(x, y, z) {
    const lat = Math.atan(Math.sinh(Math.PI*(1-2*y/(n(z)*TILE))))*180/Math.PI;
    const lon = x/(n(z)*TILE)*360-180;
    return {lat, lon:((lon+180)%360+360)%360-180};
  }
  function node(tag, css, text) {
    const e=document.createElement(tag);if(css)e.className=css;
    if(text!==undefined)e.textContent=text;return e;
  }
  window.addEventListener('DOMContentLoaded', async () => {
    const container=$('sp12-map');if(!container) return;
    // Leaflet déjà disponible : la carte de l'étape 12 fonctionne après le correctif de routes statiques.
    if (typeof window.L !== 'undefined') return;
    const caption=$('sp12-map-caption');
    const mode=$('sp12-map-mode');
    let center={lat:48.8566,lon:2.3522},zoom=12,office=null,points=[],geometry=null;
    let tileFailures=0, tileSuccess=0, firstRender=true,renderToken=0;
    try {
      const r=await fetch('/employee/api/map-context',{credentials:'same-origin'});
      if(r.ok){const data=await r.json();if(data.office){office=data.office;center={lat:office.lat,lon:office.lon};}}
    }catch{}
    container.replaceChildren();container.classList.add('sp13-map-ready');
    const tiles=node('div','sp13-map-tiles');
    const overlay=node('div','sp13-map-overlay');
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
    svg.classList.add('sp13-map-vectors');
    svg.setAttribute('aria-hidden','true');
    const pins=node('div','sp13-map-pins');
    const placeholder=node('div','sp13-map-offline', 'Chargement de la carte OpenStreetMap…');
    const controls=node('div','sp13-map-controls');
    const plus=node('button','','+');plus.type='button';plus.title='Zoom avant';plus.setAttribute('aria-label','Zoom avant');
    const minus=node('button','','−');minus.type='button';minus.title='Zoom arrière';minus.setAttribute('aria-label','Zoom arrière');
    controls.append(plus,minus);
    const attribution=node('a','sp13-map-attrib','© OpenStreetMap');
    attribution.href='https://www.openstreetmap.org/copyright';
    attribution.target='_blank';attribution.rel='noopener noreferrer';
    container.append(tiles,svg,pins,overlay,placeholder,controls,attribution);
    if(caption && !office) caption.textContent='Carte indicative centrée sur Paris (bureau non configuré). Choisis un départ et une arrivée : aucun trajet historique n’est inventé.';
    function pixel(x){const base=project(center.lat,center.lon,zoom);return{x:x.x-base.x+container.clientWidth/2,y:x.y-base.y+container.clientHeight/2};}
    function screen(point){return pixel(project(point.lat,point.lon,zoom));}
    function drawVectors(){
      svg.replaceChildren();pins.replaceChildren();
      svg.setAttribute('width',String(container.clientWidth));svg.setAttribute('height',String(container.clientHeight));
      const paths = geometry?.coordinates;
      if(Array.isArray(paths) && geometry?.type==='LineString' && paths.length>1){
        const path=document.createElementNS('http://www.w3.org/2000/svg','path');
        let data='';
        for(const v of paths){const p=screen({lon:Number(v[0]),lat:Number(v[1])});data+=(data?' L ':'M ')+p.x.toFixed(1)+' '+p.y.toFixed(1);}
        path.setAttribute('d',data);path.setAttribute('fill','none');path.setAttribute('stroke','#00a997');
        path.setAttribute('stroke-width','5');path.setAttribute('stroke-linecap','round');
        svg.append(path);
      }
      const listed=[...(office?[{...office,label:'Bureau',type:'office'}]:[]),...points.map((p,i)=>({...p,label:i===0?'Départ':'Arrivée',type:'selected'}))];
      listed.forEach(p=>{const xy=screen(p);const m=node('div','sp13-map-marker '+p.type,p.type==='office'?'⌂':p.label[0]);
        m.style.left=xy.x+'px';m.style.top=xy.y+'px';m.title=p.label;pins.append(m);});
    }
    function render(){
      if(!container.clientWidth || !container.clientHeight)return;
      tileSuccess=0;tileFailures=0;tiles.replaceChildren();
      const thisRender=++renderToken;
      if(firstRender){placeholder.hidden=false;firstRender=false;}
      const p=project(center.lat,center.lon,zoom),w=container.clientWidth,h=container.clientHeight;
      const left=p.x-w/2, top=p.y-h/2;
      const x0=Math.floor(left/TILE),x1=Math.floor((left+w)/TILE);
      const y0=Math.floor(top/TILE),y1=Math.floor((top+h)/TILE);
      for(let x=x0;x<=x1;x++)for(let y=y0;y<=y1;y++){
        if(y<0 || y>=n(zoom))continue;
        const tile=document.createElement('img');tile.alt='';tile.draggable=false;
        tile.loading='eager';tile.decoding='async';
        tile.style.left=(x*TILE-left)+'px';tile.style.top=(y*TILE-top)+'px';
        tile.src=`https://tile.openstreetmap.org/${zoom}/${(x+n(zoom))%n(zoom)}/${y}.png`;
        tile.onload=()=>{tileSuccess++;placeholder.hidden=true;};
        tile.onerror=()=>{tileFailures++;if(tileSuccess===0 && tileFailures>=2){
          placeholder.hidden=false;
          placeholder.textContent='Fond de carte indisponible. Vérifie l’accès Internet à tile.openstreetmap.org ; tu peux toujours enregistrer des activités sans cette carte.';
        }};
        tiles.append(tile);
      }
      drawVectors();
      // En cas de CDN/réseau bloqué, ne jamais laisser une carte blanche indéfiniment.
      setTimeout(()=>{
        if(thisRender===renderToken && tileSuccess===0){
          placeholder.hidden=false;
          placeholder.textContent='Fond OpenStreetMap indisponible. Vérifie la connexion à tile.openstreetmap.org ; la carte reste facultative pour le POC.';
        }
      },4500);
    }
    function toLatLon(x,y){const p=project(center.lat,center.lon,zoom);
      return invert(p.x+x-container.clientWidth/2,p.y+y-container.clientHeight/2,zoom);}
    let pointerStart=null;
    overlay.addEventListener('pointerdown',event=>{pointerStart={x:event.clientX,y:event.clientY,center:{...center}};
      try{overlay.setPointerCapture(event.pointerId);}catch{};});
    overlay.addEventListener('pointerup',event=>{
      if(!pointerStart)return;
      const dx=event.clientX-pointerStart.x,dy=event.clientY-pointerStart.y;
      const old=pointerStart;pointerStart=null;
      if(Math.hypot(dx,dy)>6){const p=project(old.center.lat,old.center.lon,zoom);
        center=invert(p.x-dx,p.y-dy,zoom);render();return;}
      const rect=overlay.getBoundingClientRect();const point=toLatLon(event.clientX-rect.left,event.clientY-rect.top);
      if(points.length>=2){points=[];geometry=null;}
      points.push(point);render();
      if(points.length===1)caption.textContent='Départ sélectionné. Clique sur le point d’arrivée pour estimer le trajet.';
      else compute();
    });
    plus.addEventListener('click',()=>{zoom=clamp(zoom+1,3,18);render();});
    minus.addEventListener('click',()=>{zoom=clamp(zoom-1,3,18);render();});
    $('sp12-map-reset')?.addEventListener('click',()=>{points=[];geometry=null;render();caption.textContent='Itinéraire effacé. Clique sur deux points pour une estimation.';});
    async function compute(){
      const [a,b]=points;
      const args=new URLSearchParams({origin_lat:a.lat,origin_lon:a.lon,
        destination_lat:b.lat,destination_lon:b.lon,mode:mode?.value||'walking'});
      caption.textContent='Calcul de l’itinéraire…';
      try {
        const r=await fetch('/geo/route?'+args,{credentials:'same-origin'});
        const data=await r.json();if(!r.ok)throw Error(data.detail||`HTTP ${r.status}`);
        geometry=data.geometry?.type==='LineString'?data.geometry:null;
        caption.textContent=`Itinéraire indicatif : ${data.distance_km} km · ${data.duration_min} min (${data.mode_label}). Ne prouve ni un déplacement historique ni un droit à la prime.`;
        drawVectors();
      }catch(e){geometry=null;drawVectors();caption.textContent='Calcul indisponible : '+e.message;}
    }
    mode?.addEventListener('change',()=>{if(points.length===2)compute();});
    const ro=typeof ResizeObserver!=='undefined'?new ResizeObserver(()=>render()):null;
    ro?.observe(container);
    render();
  });
})();
