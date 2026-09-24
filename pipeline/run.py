"""Punto de entrada: python -m pipeline.run [--data-dir ...] [--output-dir ...]"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb

from pipeline import assumptions, report
from pipeline.ingest import load_raw
from pipeline.quality import QualityCheckError, run_checks

REPO_ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = Path(__file__).resolve().parent / "sql"
LAYERS = [("staging", "10_staging.sql"), ("core", "20_core.sql"), ("marts", "30_marts.sql")]


def run_pipeline(data_dir: Path, output_dir: Path, db_path: Path | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = db_path or output_dir / "cafenorte.duckdb"
    # Cada corrida reconstruye la base completa: no hay estado de corridas previas que contamine.
    for stale in (db_path, db_path.with_name(db_path.name + ".wal")):
        stale.unlink(missing_ok=True)
    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

    with duckdb.connect(str(db_path)) as con:
        load_raw(con, data_dir)
        for layer, sql_file in LAYERS:
            con.execute((SQL_DIR / sql_file).read_text(encoding="utf-8"))
            run_checks(con, layer, run_id)
        registry = assumptions.collect(con)
        assumptions.persist(con, registry)
        report.export_csvs(con, output_dir)
        report.write_assumptions(registry, output_dir)
        report.write_answers(con, output_dir)
    return db_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipeline de ventas e inventario de CaféNorte")
    parser.add_argument("--data-dir", type=Path, default=REPO_ROOT / "data" / "raw")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "output")
    args = parser.parse_args()

    # La consola de Windows usa cp1252 por defecto y no imprime todos los acentos del catálogo.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        db_path = run_pipeline(args.data_dir, args.output_dir)
    except QualityCheckError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Base analítica: {db_path}")
    print(f"Respuestas: {args.output_dir / 'RESPUESTAS.md'}")
    print(f"Supuestos: {args.output_dir / 'SUPUESTOS.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
