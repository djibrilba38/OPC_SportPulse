"""Calcul direct des activités enregistrées pour le POC, sans QR ni preuve de code.

ATTENTION: comptabiliser pour les indicateurs du POC ne vaut PAS attribution de
prime, validation d'un trajet ou droit RH. Les sources synthétiques restent
identifiées comme telles. Ne pas utiliser dans la paie.
"""
from __future__ import annotations


def summarize_unverified_poc(data: dict, employee: dict, year: int) -> dict:
    """Enrichit un dashboard déjà chargé, sans écrire dans les bases.

    Les activités supprimées ont déjà été exclues par les requêtes sources.
    Les nouveaux enregistrements PostgreSQL du portail sont marqués decl-*;
    les enregistrements historiques dbt demeurent simulés/non justifiés.
    """
    activities = data.get("activities", [])
    seen = set()
    unique = []
    for entry in activities:
        key = str(entry.get("id", ""))
        if key in seen:
            continue
        seen.add(key)
        item = dict(entry)
        # Comptabilité POC uniquement; PAS une validation RH.
        item["counted_for_benefits"] = True
        if employee.get("is_demo"):
            item["validation_status"] = "simulated"
        elif item.get("validation_status") in ("pending", "historique_sans_validation_RH", "historique_non_validé"):
            item["validation_status"] = "simulated" if not key.startswith("decl-") else "declared"
        unique.append(item)
    data["activities"] = unique
    sports = data.setdefault("sports", {})
    n = len(unique)
    sports.update({
        "count": n,
        "declared_count": n,
        "pending_count": 0,
        "distance_km": round(sum(float(a.get("distance_km") or 0) for a in unique), 1),
        "duration_min": sum(int(a.get("duration_min") or 0) for a in unique),
        "goal": 15,
        "score": min(100, round(min(n, 15) / 15 * 100)),
        "score_label": "Progression POC : activités enregistrées (sans validation par code)",
    })
    benefits = data.setdefault("benefits", {})
    benefits["days_potential"] = 5 if n >= 15 else 0
    # Aucune prime ni journée officiellement accordée par ce calcul.
    if employee.get("is_demo"):
        benefits["bonus_estimate_eur"] = None
    if benefits.get("days_official") is None:
        benefits["wellbeing_status"] = "simulation_indicative"
    data["notices"] = [
        "POC : les activités enregistrées sont comptées sans QR/code, sans constituer une preuve RH.",
        "Les activités historiques sont synthétiques ; primes et jours bien-être restent prévisionnels.",
    ]
    return data
