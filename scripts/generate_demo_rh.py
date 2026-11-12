"""Génère exclusivement deux classeurs RH de démonstration fictifs.

Usage: python scripts/generate_demo_rh.py [--count 161] [--force]
Les classeurs produits sont ignorés par Git et attendus par le flow Kestra.
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from openpyxl import Workbook


def write_book(path: Path, headers: tuple[str, ...], rows: list[tuple], force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Fichier existant (non écrasé) : {path}. Utiliser --force si nécessaire.")
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    sheet = book.active
    sheet.title = "DONNEES_SYNTHETIQUES"
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    book.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Deux classeurs RH fictifs pour le POC SportPulse")
    parser.add_argument("--count", type=int, default=161, help="Nombre fictif d'employés (1–1000)")
    parser.add_argument("--force", action="store_true", help="Autoriser l'écrasement des fichiers de démonstration")
    args = parser.parse_args()
    if not 1 <= args.count <= 1000:
        parser.error("--count doit être entre 1 et 1000")
    folder = Path(__file__).resolve().parents[1] / "data" / "private_rh"
    rh_path = folder / "Donnees_RH.xlsx"
    sport_path = folder / "Donnees_Sportive.xlsx"
    # Vérifier les DEUX chemins avant toute écriture : éviter de laisser un état partiel.
    existing = [p for p in (rh_path, sport_path) if p.exists()]
    if existing and not args.force:
        parser.error("Les classeurs existent déjà : aucun fichier écrasé. Utiliser --force explicitement.")

    rng = random.Random(42)
    departments = ("Finance", "Ventes", "Support", "R&D", "Marketing")
    commute = ("Vélo", "Marche", "Bus", "Course à pied", "Train", "Voiture")
    sports = ("Running", "Marche", "Vélo", "")
    rows_rh: list[tuple] = []
    rows_sport: list[tuple] = []
    for n in range(1, args.count + 1):
        employee_id = f"DEMO-{n:04d}"
        dept = departments[(n - 1) % len(departments)]
        salary_eur = rng.randrange(30000, 64001, 1000)
        mode = commute[(n - 1) % len(commute)]
        activity = sports[(n - 1) % len(sports)]
        rows_rh.append((employee_id, dept, salary_eur, mode))
        rows_sport.append((employee_id, activity))

    write_book(rh_path, ("ID salarié", "BU", "Salaire brut", "Moyen de déplacement"), rows_rh, args.force)
    write_book(sport_path, ("ID salarié", "Pratique d'un sport"), rows_sport, args.force)
    print(f"Jeu 100 % synthétique généré : {args.count} salariés fictifs.")
    print("Fichiers enregistrés localement dans data/private_rh/ (exclus de Git).")


if __name__ == "__main__":
    main()
