"""Contexte non sensible de la carte OpenStreetMap.

Remplace la route hébergée auparavant par activity_proofs, afin que les
endpoints de validation QR puissent être totalement désactivés.
"""
from __future__ import annotations

import os
from typing import Annotated
from fastapi import APIRouter, Cookie
from sportpulse import employee_portal as portal

router = APIRouter(tags=["cartographie-poc"])


@router.get("/employee/api/map-context")
def map_context(session: Annotated[str | None, Cookie(alias=portal.COOKIE_NAME)] = None):
    portal.current_account(session)
    try:
        lat = float(os.environ["OFFICE_LATITUDE"])
        lon = float(os.environ["OFFICE_LONGITUDE"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Coordonnées hors plage")
        office = {"lat": lat, "lon": lon}
    except (ValueError, KeyError):
        office = None
    return {
        "office": office,
        "gps_history_available": False,
        "note": "Carte indicative uniquement. Aucun historique GPS conservé.",
    }
