"""Calcul d'itinéraires OpenStreetMap avec OSRM."""

import json
import logging
import os
import time
from functools import lru_cache
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """Erreur pendant le calcul d'un itinéraire."""


# Profils du serveur public routing.openstreetmap.de
# (avec un OSRM auto-hébergé, le profil se met dans l'URL : /route/v1/{profil}/)
PROFILES = {
    "car": "routed-car",
    "bicycle": "routed-bike",
    "scooter": "routed-bike",  # Approximation : pas de profil trottinette
    "walking": "routed-foot",
    "running": "routed-foot",
}

# Vitesses moyennes (km/h) pour recalculer la durée selon le sport.
# None = on conserve la durée renvoyée par OSRM.
SPEEDS_KMH: dict[str, float | None] = {
    "car": None,
    "bicycle": 15.0,
    "scooter": 15.0,
    "walking": 5.0,
    "running": 10.0,
}

MODE_LABELS = {
    "car": "voiture",
    "bicycle": "vélo",
    "scooter": "trottinette (approximation vélo)",
    "walking": "marche",
    "running": "course à pied",
}

_ROUTING_LOCK = Lock()
_LAST_REQUEST_AT = 0.0
MIN_REQUEST_INTERVAL = 1.05
REQUEST_TIMEOUT_S = 15


def _fetch_osrm(url: str) -> dict:
    """Appelle OSRM en respectant la limite de 1 requête/seconde."""
    global _LAST_REQUEST_AT

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "SportPulseAI/0.1",
        },
    )

    try:
        with _ROUTING_LOCK:
            delay = MIN_REQUEST_INTERVAL - (time.monotonic() - _LAST_REQUEST_AT)
            if delay > 0:
                time.sleep(delay)

            try:
                with urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                    raw = response.read()
            finally:
                # Mis à jour même en cas d'erreur : on a bien sollicité le serveur
                _LAST_REQUEST_AT = time.monotonic()

        return json.loads(raw.decode("utf-8"))

    except HTTPError as exc:
        raise RoutingError(
            f"Le service de routage a répondu HTTP {exc.code}."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        logger.warning("Routage inaccessible : %s", exc)
        raise RoutingError(
            "Service de routage inaccessible. Réessaie plus tard."
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RoutingError("Réponse OSRM invalide.") from exc


@lru_cache(maxsize=256)
def _cached_osrm_route(url: str) -> tuple[float, float, str]:
    """Cache les réponses OSRM (distance, durée, géométrie sérialisée)."""
    payload = _fetch_osrm(url)

    routes = payload.get("routes") or []
    if payload.get("code") != "Ok" or not routes:
        raise RoutingError("Aucun itinéraire n'a été trouvé.")

    route_data = routes[0]
    geometry = route_data.get("geometry")

    if (
        not isinstance(geometry, dict)
        or geometry.get("type") != "LineString"
        or not geometry.get("coordinates")
    ):
        raise RoutingError("La géométrie de l'itinéraire est invalide.")

    try:
        distance_m = float(route_data["distance"])
        duration_s = float(route_data["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RoutingError("Réponse OSRM incomplète.") from exc

    return distance_m, duration_s, json.dumps(geometry)


def calculate_route(
    origin_lon: float,
    origin_lat: float,
    destination_lon: float,
    destination_lat: float,
    mode: str = "bicycle",
) -> dict[str, object]:
    """Calcule un itinéraire et retourne mesures et géométrie GeoJSON."""

    if mode not in PROFILES:
        raise RoutingError("Mode de déplacement non pris en charge.")

    base_url = os.getenv(
        "OSRM_BASE_URL",
        "https://routing.openstreetmap.de",
    ).rstrip("/")

    # Arrondi à 5 décimales (~1 m) : améliore le taux de réussite du cache
    coordinates = (
        f"{origin_lon:.5f},{origin_lat:.5f};"
        f"{destination_lon:.5f},{destination_lat:.5f}"
    )
    query = urlencode({
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    })
    url = (
        f"{base_url}/{PROFILES[mode]}/route/v1/driving/"
        f"{coordinates}?{query}"
    )

    distance_m, osrm_duration_s, geometry_json = _cached_osrm_route(url)
    geometry = json.loads(geometry_json)

    # Durée recalculée selon la vitesse du sport (sauf voiture)
    speed_kmh = SPEEDS_KMH[mode]
    if speed_kmh is None:
        duration_s = osrm_duration_s
        note = "Durée estimée, sans données de trafic en temps réel."
    else:
        duration_s = distance_m / (speed_kmh / 3.6)
        note = (
            f"Durée estimée à {speed_kmh:g} km/h de moyenne, "
            "sans dénivelé ni trafic."
        )

    return {
        "status": "SUCCESS",
        "provider": "OpenStreetMap / OSRM",
        "mode": mode,
        "mode_label": MODE_LABELS[mode],
        "distance_m": round(distance_m, 1),
        "distance_km": round(distance_m / 1000, 2),
        "duration_s": round(duration_s, 1),
        "duration_min": round(duration_s / 60, 1),
        "geometry": geometry,
        "attribution": "© OpenStreetMap contributors",
        "note": note,
    }