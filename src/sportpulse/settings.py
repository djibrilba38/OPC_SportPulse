"""Configuration pilotée par variables d'environnement."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    environment: str
    aws_region: str
    endpoint_url: str | None
    bucket_raw: str
    bucket_artifacts: str
    source_dir: Path
    manifest_dir: Path
    aws_access_key_id: str | None
    aws_secret_access_key: str | None


def get_settings() -> Settings:
    endpoint = os.getenv("S3_ENDPOINT_URL", "").strip()

    return Settings(
        environment=os.getenv("APP_ENV", "local"),
        aws_region=os.getenv("AWS_REGION", "eu-west-3"),
        endpoint_url=endpoint or None,
        bucket_raw=os.getenv("S3_BUCKET_RAW", "sportpulse-raw"),
        bucket_artifacts=os.getenv(
            "S3_BUCKET_ARTIFACTS", "sportpulse-artifacts"
        ),
        source_dir=Path(os.getenv("SOURCE_DIR", "data/source")),
        manifest_dir=Path(os.getenv("MANIFEST_DIR", "data/manifests")),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or None,
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or None,
    )
