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
    cursor = conn.execute("SELECT * FROM repositories ORDER BY stars DESC")
    columns = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    if not output:
        date = datetime.now().strftime("%Y%m%d")
        output = f"government_repos_{date}.csv"

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(tuple(row))

    logger.info(f"Exported {len(rows)} repositories to {output}")
    return output


def export_tag_groups_csv(db_path: str, output: str | None = None) -> str:
    """Export tag groups to CSV."""
    import csv

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM tag_groups ORDER BY name")
    columns = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    if not output:
        output = "tag_groups.csv"

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(tuple(row))

    logger.info(f"Exported {len(rows)} tag groups to {output}")
    return output


def export_tag_group_members_csv(db_path: str, output: str | None = None) -> str:
    """Export tag group memberships to CSV."""
    import csv

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute("SELECT * FROM tag_group_members ORDER BY group_id, tag")
    columns = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    if not output:
        output = "tag_group_members.csv"

    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(tuple(row))

    logger.info(f"Exported {len(rows)} tag group memberships to {output}")
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
    cursor = conn.execute("SELECT * FROM repositories ORDER BY stars DESC")
    columns = [c[0] for c in cursor.description]
    rows = cursor.fetchall()
    conn.close()

    if not output:
        date = datetime.now().strftime("%Y%m%d")
        output = f"government_repos_{date}.parquet"

    # Convert to dict of lists for pyarrow
    data = {col: [row[col] for row in rows] for col in columns}
    table = pa.table(data)
    pq.write_table(table, output)

    logger.info(f"Exported {len(rows)} repositories to {output}")
    return output
