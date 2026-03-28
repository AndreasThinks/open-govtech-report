"""Export database to flat file formats."""

import json
import logging
import sqlite3
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)


def export_to_csv(db_path: str, output: str | None = None) -> str:
    """Export latest repository data to CSV."""
    import csv

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM repositories ORDER BY stars DESC").fetchall()
    conn.close()

    if not output:
        date = datetime.now().strftime("%Y%m%d")
        output = f"government_repos_{date}.csv"

    if not rows:
        logger.warning("No data to export")
        return output

    with open(output, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(rows[0].keys())
        for row in rows:
            writer.writerow(tuple(row))

    logger.info(f"Exported {len(rows)} repositories to {output}")
    return output


def export_to_parquet(db_path: str, output: str | None = None) -> str:
    """Export latest repository data to Parquet. Requires pyarrow."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        raise RuntimeError("pyarrow required for parquet export: uv add pyarrow")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM repositories ORDER BY stars DESC").fetchall()
    conn.close()

    if not output:
        date = datetime.now().strftime("%Y%m%d")
        output = f"government_repos_{date}.parquet"

    if not rows:
        logger.warning("No data to export")
        return output

    # Convert to dict of lists for pyarrow
    columns = rows[0].keys()
    data = {col: [row[col] for row in rows] for col in columns}
    table = pa.table(data)
    pq.write_table(table, output)

    logger.info(f"Exported {len(rows)} repositories to {output}")
    return output
