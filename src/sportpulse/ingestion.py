"""Découverte des fichiers sources et exécution d'un chargement traçable."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from sportpulse.settings import Settings, get_settings
from sportpulse.storage import StorageError, create_s3_client, upload_source_file

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".jsonl", ".ndjson", ".parquet"}


def discover_source_files(input_dir: Path) -> list[Path]:
    root = input_dir.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Le chemin d’entrée n’est pas un répertoire : {input_dir}")
    files = [
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not any(part.startswith(".") for part in path.relative_to(root).parts)
    ]
    return sorted(files, key=lambda item: item.relative_to(root).as_posix().lower())


def ingest_directory(
    input_dir: Path,
    *,
    settings: Settings | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    root = input_dir.resolve(strict=True)
    files = discover_source_files(root)
    if not files:
        raise ValueError(
            f"Aucun fichier source autorisé trouvé dans {root}. "
            f"Extensions prises en charge : {', '.join(sorted(SUPPORTED_EXTENSIONS))}."
        )

    s3 = client or create_s3_client(cfg)
    uploaded = []
    for file_path in files:
        stored = upload_source_file(
            file_path,
            input_root=root,
            bucket=cfg.bucket_raw,
            client=s3,
            settings=cfg,
        )
        uploaded.append({
            "filename": file_path.relative_to(root).as_posix(),
            "bucket": stored.bucket,
            "key": stored.key,
            "uri": stored.uri,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "content_type": stored.content_type,
        })

    timestamp = datetime.now(timezone.utc).replace(microsecond=0)
    run_payload: dict[str, Any] = {
        "schema_version": "1.0",
        "pipeline": "sportpulse-source-ingestion",
        "status": "SUCCESS",
        "started_at_utc": timestamp.isoformat(),
        "finished_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_count": len(uploaded),
        "bucket_raw": cfg.bucket_raw,
        "objects": uploaded,
        "note": "Le contenu des fichiers n’est pas interprété à cette étape ; les sources sont archivées à l’identique.",
    }

    cfg.manifest_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp.strftime("%Y%m%dT%H%M%SZ")
    manifest_path = cfg.manifest_dir / f"ingestion-{stamp}.json"
    # Éviter une collision rare à la seconde près.
    counter = 1
    while manifest_path.exists():
        manifest_path = cfg.manifest_dir / f"ingestion-{stamp}-{counter}.json"
        counter += 1
    manifest_path.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_key = f"ingestion-manifests/{manifest_path.name}"
    run_payload["manifest_uri"] = f"s3://{cfg.bucket_artifacts}/{manifest_key}"
    # Le manifeste distant doit contenir sa propre URI ainsi que celles des sources.
    manifest_path.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        s3.upload_file(
            str(manifest_path),
            cfg.bucket_artifacts,
            manifest_key,
            ExtraArgs={
                "ContentType": "application/json",
                "Metadata": {"pipeline": "sportpulse-source-ingestion"},
            },
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        # L'upload des sources a déjà pu aboutir : signaler l'erreur, sans déclarer la run comme complète.
        run_payload["status"] = "PARTIAL_FAILURE"
        run_payload["manifest_upload_error"] = "L’envoi du manifeste vers le bucket d’artefacts a échoué."
        manifest_path.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise StorageError("Les fichiers source ont été envoyés, mais le manifeste n’a pas pu être archivé.") from exc

    return run_payload
