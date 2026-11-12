import io
import json

import pytest

from sportpulse.load_postgres import (
    TABLE_COLUMNS,
    find_artifact_uris,
    parse_s3_uri,
    read_csv_rows,
)


class FakeS3:
    """Client S3 simulé, sans accès réseau."""

    def __init__(self, objects=None):
        self.objects = objects or {}

    def get_object(self, Bucket, Key):
        key = f"s3://{Bucket}/{Key}"
        if key not in self.objects:
            raise KeyError(f"Objet absent : {key}")

        return {"Body": io.BytesIO(self.objects[key])}


def test_parse_s3_uri_valid():
    assert parse_s3_uri(
        "s3://bucket-demo/reports/gold.json"
    ) == ("bucket-demo", "reports/gold.json")


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "https://bucket-demo/file.json",
        "s3://",
        "s3://bucket-demo/",
    ],
)
def test_parse_s3_uri_invalid(uri):
    with pytest.raises(Exception):
        parse_s3_uri(uri)


def test_find_artifact_uris_from_gold_manifest():
    manifest = {
        "pipeline": "sportpulse-silver-to-gold",
        "status": "SUCCESS",
        "outputs": {
            table: {
                "uri": (
                    "s3://artifacts/gold/runs/demo/"
                    f"aggregates/{table}.csv"
                )
            }
            for table in TABLE_COLUMNS
        },
    }

    artifacts = find_artifact_uris(manifest)

    assert set(artifacts) == set(TABLE_COLUMNS)

    for table in TABLE_COLUMNS:
        assert artifacts[table].endswith(
            f"aggregates/{table}.csv"
        )


def test_find_artifact_uris_rejects_missing_output():
    manifest = {
        "outputs": {
            "by_employee": {
                "uri": (
                    "s3://artifacts/gold/aggregates/"
                    "by_employee.csv"
                )
            }
        }
    }

    with pytest.raises(Exception):
        find_artifact_uris(manifest)


def test_read_csv_rows_validates_expected_columns():
    table = "by_employee"
    columns = TABLE_COLUMNS[table]
    header = ",".join(columns)

    # On construit un CSV avec les colonnes exactes attendues.
    values = {
        "employee_id": "EMP-TEST-001",
        "department": "Engineering",
        "activity_count": "2",
        "total_duration_min": "60.00",
        "total_distance_m": "9000.00",
        "average_duration_min": "30.00",
        "average_distance_m": "4500.00",
    }
    row = ",".join(values[column] for column in columns)
    content = f"{header}\n{row}\n".encode("utf-8")

    s3 = FakeS3({
        "s3://artifacts/gold/aggregates/by_employee.csv": content
    })

    rows = read_csv_rows(
        s3,
        "s3://artifacts/gold/aggregates/by_employee.csv",
        table,
    )

    assert len(rows) == 1
    assert rows[0]["employee_id"] == "EMP-TEST-001"
    assert rows[0]["activity_count"] == 2


def test_read_csv_rows_rejects_invalid_numeric_value():
    table = "by_employee"
    columns = TABLE_COLUMNS[table]

    values = {
        "employee_id": "EMP-TEST-001",
        "department": "Engineering",
        "activity_count": "pas-un-nombre",
        "total_duration_min": "60.00",
        "total_distance_m": "9000.00",
        "average_duration_min": "30.00",
        "average_distance_m": "4500.00",
    }
    content = (
        ",".join(columns)
        + "\n"
        + ",".join(values[column] for column in columns)
        + "\n"
    ).encode("utf-8")

    s3 = FakeS3({
        "s3://artifacts/gold/aggregates/by_employee.csv": content
    })

    with pytest.raises(Exception):
        read_csv_rows(
            s3,
            "s3://artifacts/gold/aggregates/by_employee.csv",
            table,
        )
