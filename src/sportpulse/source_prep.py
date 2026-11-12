"""Prépare les deux exports CANONIQUES attendus par Silver depuis les vrais classeurs RH.

Les fichiers Excel originaux sont archivés dans Bronze et ne sont pas copiés dans les datamarts.
Les activités créées ici sont SYNTHÉTIQUES; aucune décision RH automatique ne peut en découler.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import shutil
import unicodedata
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


def canonical(value: Any) -> str:
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', ' ', value.casefold()).strip()


def read_excel(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.suffix.lower() != '.xlsx':
        raise ValueError(f'Fichier Excel requis introuvable : {path}')
    from openpyxl import load_workbook
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        iterator = workbook.active.iter_rows(values_only=True)
        headers = [str(c).strip() if c is not None else '' for c in next(iterator)]
        if any(not h for h in headers):
            raise ValueError(f'En-tête invalide dans {path.name}')
        return [dict(zip(headers, values)) for values in iterator if any(v is not None and str(v).strip() for v in values)]
    finally:
        workbook.close()


def field(row: dict[str, Any], *aliases: str, required: bool = False) -> Any:
    index = {canonical(k): v for k, v in row.items()}
    for alias in aliases:
        value = index.get(canonical(alias))
        if value is not None and str(value).strip():
            return value
    if required:
        raise ValueError(f'Colonne obligatoire absente : {aliases[0]}')
    return None


def employee_id(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value or '').strip()


def salary(value: Any) -> str:
    raw = str(value or '').replace('\xa0', '').replace(' ', '').replace('€', '').strip()
    if raw.count(',') == 1 and '.' not in raw:
        raw = raw.replace(',', '.')
    elif ',' in raw and '.' in raw:
        raw = raw.replace(',', '')
    try:
        result = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError('Salaire brut invalide') from exc
    if not result.is_finite() or not (Decimal('0') < result < Decimal('10000000')):
        raise ValueError('Salaire brut non positif ou hors plage')
    return f'{result:.2f}'


def commute_class(value: str) -> str:
    value = canonical(value)
    if any(t in value for t in ('voiture', 'automobile', 'bus', 'metro', 'train', 'tram', 'moto', 'taxi')):
        return 'non_sport'
    if any(t in value for t in ('marche', 'running', 'course', 'velo', 'cycl', 'trottinette', 'scooter', 'skate')):
        return 'sport'
    return 'review'


def silver_declared_activity(value: str) -> str:
    """Ne pas inventer de sport : Other indique une pratique non reconnue par Silver v1."""
    val = canonical(value)
    if val in ('running', 'runing', 'course', 'course a pied', 'jogging'):
        return 'Running'
    if val in ('marche', 'walking', 'randonnee', 'hiking'):
        return 'Walking'
    if val in ('velo', 'cyclisme', 'cycling', 'bicycle'):
        return 'Cycling'
    return 'Other'


def normalize_rh(rh_rows: list[dict[str, Any]], sport_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sport_by_id: dict[str, str] = {}
    for line, row in enumerate(sport_rows, start=2):
        eid = employee_id(field(row, 'ID salarié', 'employee_id', required=True))
        if not eid or eid in sport_by_id:
            raise ValueError(f'Identifiant sportif absent ou dupliqué à la ligne {line}')
        sport_by_id[eid] = str(field(row, "Pratique d'un sport", 'declared_activity') or '').strip()

    employees = []
    seen: set[str] = set()
    for line, row in enumerate(rh_rows, start=2):
        eid = employee_id(field(row, 'ID salarié', 'employee_id', required=True))
        if not eid or eid in seen:
            raise ValueError(f'Identifiant RH absent ou dupliqué à la ligne {line}')
        seen.add(eid)
        gross = salary(field(row, 'Salaire brut', 'salaire brut annuel', 'annual_gross_salary', required=True))
        dept = str(field(row, 'BU', 'department', 'business_unit') or 'Non renseigné').strip()
        transport = str(field(row, 'Moyen de déplacement', 'declared_commute_mode') or '').strip()
        declared_sport = sport_by_id.get(eid, '')
        employees.append({
            'employee_id': eid, 'department': dept,
            'declared_activity': silver_declared_activity(declared_sport),
            'declared_sport_original': declared_sport,
            'annual_gross_salary': gross, 'commute_mode': transport,
            'commute_class': commute_class(transport),
            'sporting_commute_candidate': commute_class(transport) == 'sport',
        })
    if not employees:
        raise ValueError('Aucun salarié RH identifié')
    unknown = set(sport_by_id) - seen
    if unknown:
        raise ValueError(f'{len(unknown)} identifiant(s) sportifs sans salarié RH')
    return sorted(employees, key=lambda x: x['employee_id'])


def synthetic_activities(employees: list[dict[str, Any]], year: int, seed: int = 42) -> list[dict[str, Any]]:
    """Génère un jeu explicite de données synthétiques, stable pour année/seed/sources."""
    rng = random.Random(seed)
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    span = (end - base).days
    activities = []
    for emp in employees:
        n = rng.randint(8, 24) if emp['declared_sport_original'] else rng.randint(0, 7)
        for j in range(n):
            typ = emp['declared_activity']
            if typ == 'Other':
                # Activité entièrement fictive, et non conversion du sport déclaré.
                typ = rng.choice(('Walking', 'Running', 'Cycling'))
            duration = rng.randint(22, 100)
            if typ == 'Running':
                dist = round(rng.uniform(2500, 13000), 2)
            elif typ == 'Cycling':
                dist = round(rng.uniform(6000, 35000), 2)
            else:
                dist = round(rng.uniform(1100, 10000), 2)
            dt = base + timedelta(days=rng.randrange(span), hours=rng.choice((7, 12, 18, 19)), minutes=rng.randrange(60))
            activities.append({
                'activity_id': f'SYN-{year}-{emp["employee_id"]}-{j+1:04d}',
                'employee_id': emp['employee_id'],
                'activity_type': typ,
                'started_at': dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'duration_min': str(duration),
                'distance_m': str(dist),
            })
    return sorted(activities, key=lambda x: x['activity_id'])


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    os.chmod(path, 0o600)


def make_source_bundle(rh_path: Path, sport_path: Path, data_root: Path, year: int, kestra_run: str, seed: int = 42) -> dict[str, Any]:
    if not (2020 <= year <= 2100):
        raise ValueError('Année hors intervalle pris en charge')
    if not re.fullmatch(r'[A-Za-z0-9._-]{6,100}', kestra_run):
        raise ValueError('Identifiant de run non autorisé')
    rh = rh_path.resolve(strict=True)
    sport = sport_path.resolve(strict=True)
    staff = normalize_rh(read_excel(rh), read_excel(sport))
    sims = synthetic_activities(staff, year, seed)
    folder = data_root / 'pipeline_runs' / kestra_run
    source = folder / 'source'
    source.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(folder, 0o700)
    os.chmod(source, 0o700)
    shutil.copyfile(rh, source / 'Donnees_RH.xlsx')
    shutil.copyfile(sport, source / 'Donnees_Sportive.xlsx')
    os.chmod(source / 'Donnees_RH.xlsx', 0o600)
    os.chmod(source / 'Donnees_Sportive.xlsx', 0o600)
    write_csv(source / 'employees.csv', ['employee_id','department','declared_activity'], staff)
    write_csv(source / 'activities.csv', ['activity_id','employee_id','activity_type','started_at','duration_min','distance_m'], sims)
    snapshot = {'reference_year': year, 'records': staff, 'synthetic_activity_count': len(sims), 'simulation_seed': seed}
    snap_bytes = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode('utf-8')
    snapshot_hash = hashlib.sha256(snap_bytes).hexdigest()
    (folder/'rh_snapshot.json').write_bytes(snap_bytes)
    os.chmod(folder/'rh_snapshot.json', 0o600)
    metadata = {'reference_year': year, 'employee_count': len(staff),
                'simulated_activity_count': len(sims), 'simulation_seed': seed,
                'source_kind': 'RH_ORIGINAL_PLUS_SYNTHETIC_ACTIVITIES', 'rh_snapshot_hash': snapshot_hash}
    (source/'dataset_metadata.json').write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
    os.chmod(source/'dataset_metadata.json',0o600)
    return {'source_dir': str(source), 'rh_snapshot': str(folder/'rh_snapshot.json'), **metadata}
