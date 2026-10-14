import json

import pytest
from click.testing import CliRunner

from govtech_scraper.cli import main
from govtech_scraper.decision.evidence import WorkerStatusStore


def test_worker_status_finally_records_exception(tmp_db, tmp_path):
    status_path = tmp_path / "worker.json"
    store = WorkerStatusStore(tmp_db, worker_name="classifier", status_path=status_path)
    with pytest.raises(RuntimeError):
        with store.run(phase="running", model="openai/gpt-6-luna", run_id="run-1"):
            store.update(phase="running", labelled=10, pending_reconciliation=1)
            raise RuntimeError("boom")

    payload = json.loads(status_path.read_text())
    assert payload["phase"] == "failed"
    assert payload["final_error"] == "boom"
    assert payload["last_committed_counts"]["labelled"] == 10
    assert payload["pending_reconciliation"] == 1


def test_exclusive_worker_lock_prevents_duplicate_paid_workers(tmp_db):
    first = WorkerStatusStore(tmp_db, worker_name="classifier")
    second = WorkerStatusStore(tmp_db, worker_name="classifier")
    with first.run(phase="running", model="openai/gpt-6-luna", run_id="same"):
        with pytest.raises(RuntimeError, match="already running"):
            with second.run(phase="running", model="openai/gpt-6-luna", run_id="same"):
                pass


def test_status_cli_reports_terminal_state(tmp_path):
    db_path = tmp_path / "status.db"
    from govtech_scraper.db import Database

    db = Database(str(db_path))
    try:
        store = WorkerStatusStore(db, worker_name="classifier")
        store.write_terminal(phase="blocked", final_error="auth failed", counts={"labelled": 1194})
    finally:
        db.close()

    result = CliRunner().invoke(main, ["--db", str(db_path), "decide", "worker-status", "--worker", "classifier"])
    assert result.exit_code == 2
    assert "blocked" in result.output
    assert "auth failed" in result.output
