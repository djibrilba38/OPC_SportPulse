"""API SportPulse AI : santé, supervision et itinéraires."""

import logging
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sportpulse.employee_portal import router as employee_portal_router
from sportpulse.rag import router as rag_router
from sportpulse.admin_portal import router as admin_router

from sportpulse import __version__
from sportpulse.geography import RoutingError, calculate_route
from sportpulse.map_page import MAP_HTML
from sportpulse.settings import get_settings
from sportpulse.storage import StorageError, check_bucket

logger = logging.getLogger(__name__)

app = FastAPI(
    title="SportPulse AI",
    version=__version__,
    description=(
        "API de santé, de supervision et de calcul d'itinéraires "
        "OpenStreetMap."
    ),
)
app.include_router(employee_portal_router)
app.include_router(rag_router)
app.include_router(admin_router)
# Métriques Prometheus exposées sur /metrics (hors documentation OpenAPI)
Instrumentator().instrument(app).expose(
    app,
    endpoint="/metrics",
    include_in_schema=False,
)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Vérifie que l'API répond."""
    return {
        "status": "ok",
        "service": "sportpulse-ai",
        "version": __version__,
    }


@app.get("/health/storage", tags=["health"])
def storage_health() -> dict[str, object]:
    """Vérifie l'accès aux buckets de stockage S3-compatible."""
    settings = get_settings()

    try:
        check_bucket(settings.bucket_raw)
        check_bucket(settings.bucket_artifacts)
    except StorageError as exc:
        # Détail technique dans les logs, message générique pour le client
        logger.error("Stockage indisponible : %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Stockage indisponible.",
        ) from exc

    return {
        "status": "ok",
        "provider": "s3-compatible",
        "bucket_raw": settings.bucket_raw,
        "bucket_artifacts": settings.bucket_artifacts,
    }


@app.get(
    "/map",
    response_class=HTMLResponse,
    tags=["geography"],
    include_in_schema=False,
)
def openstreetmap_page() -> HTMLResponse:
    """Affiche l'interface cartographique."""
    return HTMLResponse(content=MAP_HTML)


@app.get(
    "/geo/route",
    tags=["geography"],
    responses={
        422: {"description": "Départ et arrivée identiques ou paramètres invalides"},
        502: {"description": "Service de routage indisponible ou réponse invalide"},
    },
)
def route(
    origin_lon: float = Query(..., ge=-180, le=180, description="Longitude du départ"),
    origin_lat: float = Query(..., ge=-90, le=90, description="Latitude du départ"),
    destination_lon: float = Query(..., ge=-180, le=180, description="Longitude de l'arrivée"),
    destination_lat: float = Query(..., ge=-90, le=90, description="Latitude de l'arrivée"),
    mode: Literal["car", "bicycle", "scooter", "walking", "running"] = "bicycle",
) -> dict[str, object]:
    """Calcule un itinéraire indicatif entre deux coordonnées."""

    if origin_lon == destination_lon and origin_lat == destination_lat:
        raise HTTPException(
            status_code=422,
            detail="Le départ et l'arrivée doivent être différents.",
        )

    try:
        return calculate_route(
            origin_lon=origin_lon,
            origin_lat=origin_lat,
            destination_lon=destination_lon,
            destination_lat=destination_lat,
            mode=mode,
        )
    except RoutingError as exc:
        logger.warning("Échec du calcul d'itinéraire (%s) : %s", mode, exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
# Étape 7 : scénarios RH et corrections d'activités

from sportpulse.activity_management import router as activity_management_router

app.include_router(activity_management_router)

# SportPulse étape 9 : reporting annuel admin
from sportpulse.annual_reporting import router as annual_reporting_router
app.include_router(annual_reporting_router)

# Étape 10 — RH fourni au POC, accès administrateur authentifié
from sportpulse.rh_dashboard import router as rh_dashboard_router
app.include_router(rh_dashboard_router)

# SPORTPULSE_STEP12_PROOFS — code QR RH + lecture carte, jamais de rémunération automatique.
# SPORTPULSE_STEP13 : administration allégée, accès aux journaux archivé.
from sportpulse.admin_history import router as admin_history_router
app.include_router(admin_history_router)

# SPORTPULSE_STEP14_LINK : comptes RH, code court, replay intégré.
from sportpulse.rh_portal_bridge import router as rh_portal_bridge_router
app.include_router(rh_portal_bridge_router)

# Étape 15 : contexte cartographique sans module QR.
from sportpulse.poc_map import router as poc_map_router
app.include_router(poc_map_router)

# SPORTPULSE_STEP16_PERSONAL_BENEFITS : lecture RH personnelle sécurisée.
from sportpulse.employee_benefits import router as employee_benefits_router
app.include_router(employee_benefits_router)

# SPORTPULSE_STEP17_SYNC_ROUTER
from sportpulse.employee_activity_sync import router as step17_sync_router
app.include_router(step17_sync_router)
