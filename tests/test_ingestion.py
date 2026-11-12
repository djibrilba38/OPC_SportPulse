import json
from pathlib import Path

from sportpulse.ingestion import discover_source_files, ingest_directory
from sportpulse.settings import Settings


class FakeS3:
    def __init__(self):
        self.uploads = []
        self.uploaded_text = {}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        file_path = Path(filename)
        self.uploads.append((file_path.name, bucket, key, ExtraArgs or {}))
        if file_path.suffix == ".json":
            self.uploaded_text[(bucket, key)] = file_path.read_text(encoding="utf-8")


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        aws_region="us-east-1",
        endpoint_url="http://minio:9000",
        bucket_raw="sportpulse-raw",
        bucket_artifacts="sportpulse-artifacts",
        source_dir=tmp_path / "source",
        manifest_dir=tmp_path / "manifests",
        aws_access_key_id="fake",
        aws_secret_access_key="fake",
    )


def test_discovery_only_accepts_data_extensions(tmp_path: Path):
    (tmp_path / "one.csv").write_text("a\n1\n")
    (tmp_path / "secret.env").write_text("NOT_A_SOURCE=1\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "two.xlsx").write_bytes(b"test")

    found = discover_source_files(tmp_path)
    assert {path.relative_to(tmp_path).as_posix() for path in found} == {"one.csv", "nested/two.xlsx"}


def test_ingestion_uploads_sources_and_manifest(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "activity.csv").write_text("activity_id\nACT-1\n", encoding="utf-8")
    settings = make_settings(tmp_path)
    client = FakeS3()

    result = ingest_directory(source, settings=settings, client=client)

    assert result["status"] == "SUCCESS"
    assert result["source_count"] == 1
    assert result["objects"][0]["uri"].startswith("s3://sportpulse-raw/source_files/sha256=")
    assert result["manifest_uri"].startswith("s3://sportpulse-artifacts/ingestion-manifests/")
    assert len(client.uploads) == 2
    remote_manifest_entry = next(item for item in client.uploads if item[1] == settings.bucket_artifacts)
    remote_manifest = json.loads(client.uploaded_text[(remote_manifest_entry[1], remote_manifest_entry[2])])
    assert remote_manifest["manifest_uri"] == result["manifest_uri"]
    local_manifests = list(settings.manifest_dir.glob("ingestion-*.json"))
    assert len(local_manifests) == 1
    saved = json.loads(local_manifests[0].read_text(encoding="utf-8"))
    assert saved["source_count"] == 1


def test_empty_source_directory_fails_clearly(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    settings = make_settings(tmp_path)

    try:
        ingest_directory(source, settings=settings, client=FakeS3())
    except ValueError as exc:
        assert "Aucun fichier source" in str(exc)
    else:
        raise AssertionError("Une entrée vide doit être signalée.")
