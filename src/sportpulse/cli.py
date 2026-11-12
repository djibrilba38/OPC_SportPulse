
"""Interface en ligne de commande de SportPulse AI."""
import argparse
import json
import sys
from pathlib import Path

from sportpulse.storage import StorageError


def print_error(exc: Exception) -> int:
    print(
        json.dumps(
            {"status": "FAILED", "error": str(exc)},
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sportpulse",
        description="Ingestion et traitement analytique SportPulse AI",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="Archiver les sources vers S3")
    ingest.add_argument("--input-dir", default=None)

    transform = commands.add_parser(
        "transform", help="Valider les sources et produire la couche Silver"
    )
    transform.add_argument("--manifest-uri", required=True)

    gold = commands.add_parser(
        "gold", help="Calculer les indicateurs métier depuis Silver"
    )
    gold.add_argument("--manifest-uri", required=True)

    args = parser.parse_args()

    try:
        if args.command == "ingest":
            from sportpulse.ingestion import ingest_directory
            from sportpulse.settings import get_settings

            settings = get_settings()
            input_dir = Path(args.input_dir) if args.input_dir else settings.source_dir
            result = ingest_directory(input_dir, settings=settings)

        elif args.command == "transform":
            from sportpulse.transform import transform_from_manifest

            result = transform_from_manifest(args.manifest_uri)

        elif args.command == "gold":
            from sportpulse.analytics import build_gold_from_manifest

            result = build_gold_from_manifest(args.manifest_uri)

        else:
            parser.error("Commande inconnue.")

    except (StorageError, ValueError, RuntimeError, OSError) as exc:
        return print_error(exc)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
