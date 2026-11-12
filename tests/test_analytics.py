import csv
import io
import json
from pathlib import Path
from io import BytesIO

import pytest

from sportpulse.analytics import AnalyticsError, build_gold_from_manifest
from sportpulse.settings import Settings


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_object(self, Bucket, Key):
        return {"Body": BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = (
            Body.encode() if isinstance(Body, str) else Body
        )


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        aws_region="eu-west-3",
        endpoint_url=None,
        bucket_raw="sportpulse-raw",
        bucket_artifacts="sportpulse-artifacts",
        source_dir=tmp_path / "source",
        manifest_dir=tmp_path / "manifests",
        aws_access_key_id=None,
        aws_secret_access_key=None,
    )


def prepare_silver(s3: FakeS3, bucket: str) -> str:
    run = "silver-test"
    prefix = f"silver/runs/{run}/"
    s3.objects[(bucket, prefix + "activities.csv")] = (
        "activity_id,employee_id,activity_type,started_at,duration_min,distance_m\n"
        "A1,E1,Running,2026-10-01T08:00:00Z,30,5000\n"
        "A2,E1,Cycling,2026-10-01T09:00:00Z,45,10000\n"
        "A3,E2,Walking,2026-10-01T10:00:00Z,20,2500\n"
        "A4,E3,Running,2026-10-02T08:00:00Z,36,8400\n"
    ).encode()
    s3.objects[(bucket, prefix + "employees.csv")] = (
        "employee_id,department,declared_activity\n"
        "E1,Engineering,Running\nE2,Engineering,Walking\nE3,HR,Running\n"
    ).encode()

    key = "transform-manifests/transform-silver-test.json"
    s3.objects[(bucket, key)] = json.dumps({
        "pipeline": "sportpulse-bronze-to-silver",
        "status": "SUCCESS",
        "run_id": run,
        "outputs": {
            name: {"uri": f"s3://{bucket}/{prefix}{name}.csv"}
            for name in ("activities", "employees")
        },
    }).encode()
    return f"s3://{bucket}/{key}"


def output_rows(s3: FakeS3, result: dict, name: str) -> list[dict]:
    key = result["outputs"][name]["key"]
    return list(csv.DictReader(io.StringIO(
        s3.objects[("sportpulse-artifacts", key)].decode()
    )))


def test_gold_metrics_and_aggregates(tmp_path: Path):
    settings, s3 = make_settings(tmp_path), FakeS3()
    result = build_gold_from_manifest(
        prepare_silver(s3, settings.bucket_artifacts),
        settings=settings, client=s3,
    )

    assert result["status"] == "SUCCESS"
    assert result["metrics"] == {
        "activity_count": 4,
        "employee_count": 3,
        "active_employee_count": 3,
        "total_duration_min": 131.0,
        "total_distance_m": 25900.0,
        "average_duration_min": 32.75,
        "average_distance_m": 6475.0,
    }

    employees = output_rows(s3, result, "by_employee")
    e1 = next(row for row in employees if row["employee_id"] == "E1")
    assert (e1["activity_count"], e1["total_duration_min"],
            e1["total_distance_m"]) == ("2", "75.00", "15000.00")

    departments = output_rows(s3, result, "by_department")
    eng = next(row for row in departments if row["department"] == "Engineering")
    assert (eng["employee_count"], eng["active_employee_count"],
            eng["activity_count"], eng["total_duration_min"]) == (
                "2", "2", "3", "95.00"
            )

    sports = output_rows(s3, result, "by_activity_type")
    running = next(row for row in sports if row["activity_type"] == "Running")
    assert (running["activity_count"], running["total_distance_m"]) == (
        "2", "13400.00"
    )

    days = output_rows(s3, result, "by_day")
    assert len(days) == 2
    assert (days[0]["activity_date"], days[0]["activity_count"]) == (
        "2026-10-01", "3"
    )


def test_gold_rejects_activity_with_unknown_employee(tmp_path: Path):
    settings, s3 = make_settings(tmp_path), FakeS3()
    manifest = prepare_silver(s3, settings.bucket_artifacts)
    key = "silver/runs/silver-test/activities.csv"
    content = s3.objects[(settings.bucket_artifacts, key)].decode()
    s3.objects[(settings.bucket_artifacts, key)] = content.replace(
        "A1,E1,", "A1,E999,", 1
    ).encode()

    with pytest.raises(AnalyticsError, match="employé absent"):
        build_gold_from_manifest(manifest, settings=settings, client=s3)
