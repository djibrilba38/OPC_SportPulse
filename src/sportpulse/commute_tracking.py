
"""Suivi des trajets sportifs et contrôle d'arrivée par QR code."""

import hashlib
import hmac
import io
import math
import os
import time
from datetime import datetime, timezone
from typing import Literal

import psycopg
import qrcode
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

router = APIRouter(tags=["commute"])

TransportMode = Literal[
    "bicycle", "scooter", "running", "walking"
]


class CommuteSubmission(BaseModel):
    employee_id: str = Field(min_length=1, max_length=128)
    transport_mode: TransportMode
    started_at: datetime
    ended_at: datetime
    gps_samples_count: int = Field(ge=2, le=100000)
    gps_distance_m: float = Field(gt=0, le=200000)
    gps_accuracy_p95_m: float | None = Field(
        default=None, gt=0, le=10000
    )
    end_lat: float = Field(ge=-90, le=90)
    end_lon: float = Field(ge=-180, le=180)
    arrival_qr_token: str = Field(min_length=20, max_length=256)


def _distance_m(lat1: float, lon1: float,
                lat2: float, lon2: float) -> float:
    """Distance géographique entre deux points, en mètres."""
    radius = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = (
        math.sin(dp / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(min(1.0, a)))


def _new_qr_token() -> str:
    secret = os.getenv("OFFICE_ARRIVAL_QR_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Le secret de validation QR n'est pas configuré.",
        )

    timestamp = str(int(time.time()))
    signature = hmac.new(
        secret.encode(), timestamp.encode(), hashlib.sha256
    ).hexdigest()
    return f"{timestamp}.{signature}"


def _valid_qr_token(token: str) -> bool:
    secret = os.getenv("OFFICE_ARRIVAL_QR_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="Le secret de validation QR n'est pas configuré.",
        )

    try:
        timestamp_text, signature = token.strip().split(".", 1)
        timestamp = int(timestamp_text)
    except (ValueError, TypeError):
        return False

    # QR valide cinq minutes au maximum.
    if abs(time.time() - timestamp) > 300:
        return False

    expected = hmac.new(
        secret.encode(), timestamp_text.encode(), hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected)


@router.get("/geo/commute", response_class=HTMLResponse)
def commute_page():
    """Page mobile de déclaration d'un trajet."""
    return HTMLResponse(COMMUTE_HTML)


@router.get("/geo/commute/office-qr-admin",
            response_class=HTMLResponse)
def office_qr_admin_page():
    """Interface de génération du QR à afficher dans les locaux."""
    return HTMLResponse(QR_ADMIN_HTML)


@router.post("/geo/commute/office-qr.png")
def generate_office_qr(
    x_office_admin_key: str = Header(
        default="", alias="X-Office-Admin-Key"
    ),
):
    """Génère un QR temporaire. L'accès est protégé par une clé admin."""
    admin_key = os.getenv("OFFICE_QR_ADMIN_KEY", "")

    if len(admin_key) < 32:
        raise HTTPException(
            status_code=503,
            detail="La clé administrateur QR n'est pas configurée.",
        )

    if not hmac.compare_digest(x_office_admin_key, admin_key):
        raise HTTPException(status_code=403, detail="Accès refusé.")

    token = _new_qr_token()
    image = qrcode.make(token)
    output = io.BytesIO()
    image.save(output, format="PNG")

    return Response(
        content=output.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/geo/commute/submit")
def submit_commute(data: CommuteSubmission):
    """Enregistre les mesures utiles, sans enregistrer de trace GPS brute."""

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL n'est pas configurée.",
        )

    try:
        office_lat = float(os.environ["OFFICE_LATITUDE"])
        office_lon = float(os.environ["OFFICE_LONGITUDE"])
        radius_m = float(os.getenv("OFFICE_GEOFENCE_RADIUS_M", "150"))
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Les coordonnées de référence du bureau ne sont pas configurées.",
        )

    if not (-90 <= office_lat <= 90 and -180 <= office_lon <= 180):
        raise HTTPException(
            status_code=503,
            detail="Les coordonnées du bureau sont invalides.",
        )

    if not _valid_qr_token(data.arrival_qr_token):
        raise HTTPException(
            status_code=403,
            detail="QR invalide ou expiré. Scannez le QR actuel du bureau.",
        )

    if (
        data.started_at.tzinfo is None
        or data.ended_at.tzinfo is None
    ):
        raise HTTPException(
            status_code=422,
            detail="Les dates doivent inclure un fuseau horaire.",
        )

    started = data.started_at.astimezone(timezone.utc)
    ended = data.ended_at.astimezone(timezone.utc)
    duration_s = (ended - started).total_seconds()

    if duration_s <= 0 or ended.timestamp() > time.time() + 60:
        raise HTTPException(
            status_code=422,
            detail="La chronologie du trajet est invalide.",
        )

    office_distance = _distance_m(
        data.end_lat, data.end_lon, office_lat, office_lon
    )
    office_verified = office_distance <= radius_m

    max_speed_m_s = {
        "bicycle": 20.0,
        "scooter": 15.0,
        "running": 8.0,
        "walking": 5.5,
    }[data.transport_mode]

    avg_speed = data.gps_distance_m / duration_s

    # Filtre de cohérence technique, pas une règle RH.
    plausible = (
        60 <= duration_s <= 21600
        and data.gps_samples_count >= 3
        and 100 <= data.gps_distance_m <= 150000
        and data.gps_accuracy_p95_m is not None
        and data.gps_accuracy_p95_m <= 50
        and avg_speed <= max_speed_m_s
    )

    review_status = (
        "pending" if office_verified and plausible
        else "requires_review"
    )

    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1
                    FROM dbt_marts.dim_employee
                    WHERE employee_id = %s
                    """,
                    (data.employee_id,),
                )
                if cur.fetchone() is None:
                    raise HTTPException(
                        status_code=404,
                        detail="Employé introuvable dans le référentiel.",
                    )

                cur.execute(
                    """
                    INSERT INTO benefits.sport_commute_declarations (
                        employee_id, commute_date, transport_mode,
                        office_arrival_confirmed, validation_status
                    )
                    VALUES (%s, %s, %s, %s, 'pending')
                    RETURNING commute_id
                    """,
                    (
                        data.employee_id,
                        started.date(),
                        data.transport_mode,
                        office_verified,
                    ),
                )
                commute_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO benefits.commute_evidence (
                        commute_id, started_at, ended_at,
                        gps_samples_count, gps_distance_m,
                        gps_accuracy_p95_m,
                        office_geofence_verified,
                        arrival_qr_verified,
                        plausibility_passed,
                        verification_status,
                        reviewer_note
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, TRUE, %s, %s, %s
                    )
                    """,
                    (
                        commute_id,
                        started,
                        ended,
                        data.gps_samples_count,
                        data.gps_distance_m,
                        data.gps_accuracy_p95_m,
                        office_verified,
                        plausible,
                        review_status,
                        "Contrôles techniques ; validation RH obligatoire.",
                    ),
                )

    except HTTPException:
        raise
    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Impossible d'enregistrer la preuve de trajet.",
        )

    return {
        "status": "SUCCESS",
        "commute_id": commute_id,
        "verification_status": review_status,
        "office_geofence_verified": office_verified,
        "arrival_qr_verified": True,
        "plausibility_passed": plausible,
        "gps_raw_points_persisted": False,
        "message": (
            "Trajet enregistré en attente de validation RH."
            if review_status == "pending"
            else "Trajet enregistré pour contrôle complémentaire RH."
        ),
    }


COMMUTE_HTML = r"""
<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SportPulse AI — Trajet sportif</title>
<style>
body{font-family:Arial,sans-serif;max-width:620px;margin:25px auto;padding:0 16px;color:#17365d}
section{border:1px solid #d7e2ec;border-radius:12px;padding:18px;margin:14px 0}
input,select,button{width:100%;box-sizing:border-box;padding:12px;margin:7px 0;border-radius:6px;border:1px solid #c6d4e0}
button{background:#087e8b;color:white;border:0;cursor:pointer}
button:disabled{opacity:.5}
.small{font-size:13px;color:#52677e}
#status{white-space:pre-wrap;line-height:1.5}
</style>
</head>
<body>
<h1>SportPulse AI</h1>
<h2>Trajet sportif domicile-bureau</h2>
<p class="small">Prototype : points GPS conservés uniquement en mémoire dans le navigateur.
Seules les mesures résumées sont envoyées à l'API.</p>

<section>
<label>Identifiant salarié (compte de démonstration)</label>
<input id="employee" placeholder="EMP-DEMO-001" autocomplete="off">
<label>Mode de déplacement</label>
<select id="mode">
<option value="bicycle">Vélo</option>
<option value="scooter">Trottinette (approximation vélo)</option>
<option value="running">Course à pied</option>
<option value="walking">Marche</option>
</select>
<button id="start">Démarrer le trajet</button>
<button id="stop" disabled>Terminer le trajet</button>
<div id="status" aria-live="polite">En attente du démarrage.</div>
</section>

<section>
<h3>Confirmer l'arrivée au bureau</h3>
<p class="small">Scannez le QR temporaire affiché dans les locaux, ou collez son contenu.</p>
<button id="scan" disabled>Scanner le QR d'arrivée</button>
<div id="qr-reader" style="width:100%;max-width:360px"></div>
<input id="qrToken" placeholder="Code du QR (si saisie manuelle)">
<button id="submit" disabled>Envoyer pour validation RH</button>
</section>

<script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script>
let watchId=null, points=[], totalDistance=0, startedAt=null, lastPoint=null;
let endedAt=null, stopped=false, scanner=null;

const $ = id => document.getElementById(id);
const status = text => $('status').textContent = text;

function haversine(a,b){
 const r=6371000, rad=x=>x*Math.PI/180;
 const dp=rad(b.lat-a.lat), dl=rad(b.lon-a.lon);
 const x=Math.sin(dp/2)**2+
   Math.cos(rad(a.lat))*Math.cos(rad(b.lat))*Math.sin(dl/2)**2;
 return 2*r*Math.asin(Math.sqrt(Math.min(1,x)));
}

function onPosition(pos){
 const c=pos.coords;
 if(c.accuracy > 100){
   status("GPS imprécis ("+Math.round(c.accuracy)+" m). Recherche d'un meilleur signal…");
   return;
 }
 const point={lat:c.latitude,lon:c.longitude,accuracy:c.accuracy};
 if(lastPoint) totalDistance+=haversine(lastPoint,point);
 points.push(point);
 lastPoint=point;
 status("Trajet en cours\nPoints GPS exploitables : "+points.length+
   "\nDistance mesurée : "+(totalDistance/1000).toFixed(2)+" km"+
   "\nPrécision dernière mesure : "+Math.round(c.accuracy)+" m");
}

$('start').onclick=()=>{
 if(!$('employee').value.trim()){
   status("Saisissez votre identifiant de démonstration.");
   return;
 }
 if(!navigator.geolocation){
   status("La géolocalisation n'est pas disponible dans ce navigateur.");
   return;
 }
 points=[]; totalDistance=0; lastPoint=null; stopped=false;
 startedAt=new Date().toISOString();
 watchId=navigator.geolocation.watchPosition(
   onPosition,
   err=>status("Erreur GPS : "+err.message),
   {enableHighAccuracy:true,maximumAge:0,timeout:15000}
 );
 $('start').disabled=true; $('stop').disabled=false;
 status("Autorisation GPS demandée. Déplacez-vous puis terminez le trajet.");
};

$('stop').onclick=()=>{
 if(watchId!==null) navigator.geolocation.clearWatch(watchId);
 watchId=null; endedAt=new Date().toISOString(); stopped=true;
 $('stop').disabled=true; $('scan').disabled=false; $('submit').disabled=false;
 status("Trajet terminé.\nPoints mesurés : "+points.length+
   "\nDistance GPS : "+(totalDistance/1000).toFixed(2)+" km"+
   "\nScannez ensuite le QR d'arrivée du bureau.");
};

$('scan').onclick=async()=>{
 if(!window.Html5Qrcode){
   status("Scanner indisponible. Collez manuellement le contenu du QR.");
   return;
 }
 try{
   scanner=new Html5Qrcode("qr-reader");
   await scanner.start(
     {facingMode:"environment"},{fps:10,qrbox:220},
     async decoded=>{
       $('qrToken').value=decoded;
       await scanner.stop();
       scanner=null;
       status("QR lu. Vous pouvez envoyer la déclaration.");
     },
     ()=>{}
   );
 }catch(e){
   status("Caméra indisponible. Autorisez-la ou collez le code QR manuellement.");
 }
};

$('submit').onclick=async()=>{
 if(!stopped || points.length<2 || !lastPoint){
   status("Trajet insuffisant : au moins deux positions GPS sont nécessaires.");
   return;
 }
 if(totalDistance<=0){
   status("Distance nulle : le trajet ne peut pas être envoyé.");
   return;
 }
 if(!$('qrToken').value.trim()){
   status("Scannez ou collez le code QR d'arrivée.");
   return;
 }

 const acc=points.map(p=>p.accuracy).sort((a,b)=>a-b);
 const p95=acc[Math.min(acc.length-1,Math.ceil(.95*acc.length)-1)];

 const body={
   employee_id:$('employee').value.trim(),
   transport_mode:$('mode').value,
   started_at:startedAt,
   ended_at:endedAt,
   gps_samples_count:points.length,
   gps_distance_m:Math.round(totalDistance),
   gps_accuracy_p95_m:Number(p95.toFixed(2)),
   end_lat:lastPoint.lat,
   end_lon:lastPoint.lon,
   arrival_qr_token:$('qrToken').value.trim()
 };

 $('submit').disabled=true;
 status("Envoi du résumé et vérification…");

 try{
   const response=await fetch('/geo/commute/submit',{
     method:'POST',
     headers:{'Content-Type':'application/json'},
     body:JSON.stringify(body)
   });
   const result=await response.json();
   if(!response.ok) throw new Error(result.detail||"Échec de l'envoi.");

   // Efface les points du navigateur après l'envoi.
   points=[]; lastPoint=null; totalDistance=0;
   status(JSON.stringify(result,null,2));
 }catch(e){
   $('submit').disabled=false;
   status("Erreur : "+e.message);
 }
};
</script>
</body>
</html>
"""


QR_ADMIN_HTML = r"""
<!doctype html>
<html lang="fr">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SportPulse AI — QR bureau</title>
<style>
body{font-family:Arial,sans-serif;max-width:560px;margin:30px auto;padding:0 16px;color:#17365d}
input,button{width:100%;box-sizing:border-box;padding:12px;margin:8px 0}
button{background:#087e8b;color:white;border:0;cursor:pointer}
img{display:block;width:280px;max-width:100%;margin:18px auto}
</style></head>
<body>
<h1>QR d'arrivée au bureau</h1>
<p>Cette page est destinée à l'écran autorisé des locaux. Le QR expire après cinq minutes.</p>
<input id="key" type="password" placeholder="Clé administrateur QR">
<button id="generate">Afficher / actualiser le QR</button>
<img id="qr" alt="QR temporaire d'arrivée" hidden>
<p id="status"></p>
<script>
let timer=null, previousUrl=null;
async function refresh(){
 const key=document.getElementById('key').value;
 if(!key){document.getElementById('status').textContent='Saisissez la clé admin.';return;}
 try{
  const res=await fetch('/geo/commute/office-qr.png',{
   method:'POST',headers:{'X-Office-Admin-Key':key}
  });
  if(!res.ok) throw new Error('Erreur HTTP '+res.status);
  const blob=await res.blob();
  if(previousUrl) URL.revokeObjectURL(previousUrl);
  previousUrl=URL.createObjectURL(blob);
  const img=document.getElementById('qr');
  img.src=previousUrl;img.hidden=false;
  document.getElementById('status').textContent='QR généré. Renouvellement automatique chaque minute.';
 }catch(e){document.getElementById('status').textContent=e.message;}
}
document.getElementById('generate').onclick=()=>{
 if(timer) clearInterval(timer);
 refresh();timer=setInterval(refresh,60000);
};
</script>
</body>
</html>
"""
