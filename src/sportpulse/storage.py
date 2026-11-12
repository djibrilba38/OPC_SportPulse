"""Adaptateur S3 compatible AWS S3 et MinIO local."""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from sportpulse.settings import Settings, get_settings


class StorageError(RuntimeError):
    """Erreur fonctionnelle d'accès au stockage objet."""


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    uri: str
    sha256: str
    size_bytes: int
    content_type: str


def create_s3_client(settings: Settings | None = None) -> Any:
    cfg = settings or get_settings()
    kwargs: dict[str, Any] = {
        "service_name": "s3",
        "region_name": cfg.aws_region,
    }
    if cfg.endpoint_url:
        kwargs["endpoint_url"] = cfg.endpoint_url
    if cfg.aws_access_key_id:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
    if cfg.aws_secret_access_key:
        kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
    return boto3.client(**kwargs)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upload_source_file(
    path: Path,
    *,
    input_root: Path,
    bucket: str,
    client: Any | None = None,
    settings: Settings | None = None,
) -> StoredObject:
    """Archive un fichier source sous une clé versionnée par son empreinte.

    Le hash dans la clé évite d'écraser silencieusement une version différente
    d'un même fichier. Les données sont envoyées telles quelles, sans les lire.
    """
    cfg = settings or get_settings()
    s3 = client or create_s3_client(cfg)
    resolved_file = path.resolve(strict=True)
    resolved_root = input_root.resolve(strict=True)
    if not resolved_file.is_file() or resolved_root not in resolved_file.parents:
        raise StorageError("Le fichier source doit être un fichier situé sous le répertoire d'entrée autorisé.")

    digest = sha256_file(resolved_file)
    relative_name = resolved_file.relative_to(resolved_root).as_posix()
    key = f"source_files/sha256={digest}/{relative_name}"
    content_type = mimetypes.guess_type(resolved_file.name)[0] or "application/octet-stream"
    try:
        s3.upload_file(
            str(resolved_file),
            bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "Metadata": {"sha256": digest},
            },
        )
    except (BotoCoreError, ClientError, OSError) as exc:
        # Ne pas inclure les identifiants de connexion ni les détails de requête dans le message.
        raise StorageError(f"Échec de l’envoi du fichier source '{resolved_file.name}' vers le stockage objet.") from exc

    return StoredObject(
        bucket=bucket,
        key=key,
        uri=f"s3://{bucket}/{key}",
        sha256=digest,
        size_bytes=resolved_file.stat().st_size,
        content_type=content_type,
    )


def check_bucket(bucket: str, client: Any | None = None, settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    s3 = client or create_s3_client(cfg)
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except (BotoCoreError, ClientError) as exc:
        raise StorageError(f"Le bucket configuré '{bucket}' est inaccessible ou n’existe pas.") from exc
