"""Explicit recurring authority layered over the existing durable billing ledger.

Approval is a local administrative operation, never an implicit side effect of a
worker start. All amounts are USD. No historical ledger/config rows are rewritten.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import UTC, datetime
import hashlib
import json
import math
from typing import Callable

from ..db import Database
from .evidence import BudgetExhausted, OpenRouterBudget, ensure_evidence_schema


@dataclass(frozen=True)
class RecurringAuthorization:
    authorization_id: str
    monthly_usd: float
    run_usd: float
    approved_at: str
    effective_at: str

    def serialized(self) -> str:
        if not self.authorization_id or self.authorization_id == 'legacy-one-off':
            raise BudgetExhausted('Invalid recurring authorization ID')
        for value, maximum in ((self.monthly_usd, 5), (self.run_usd, 2)):
            if not math.isfinite(value) or not 0 < value <= maximum:
                raise BudgetExhausted('Recurring caps may not exceed $5/month and $2/run')
        if _utc(self.effective_at) < _utc(self.approved_at):
            raise BudgetExhausted('Effective timestamp precedes approval')
        return json.dumps(asdict(self), sort_keys=True)


def _utc(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError('timezone required')
        return result.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise BudgetExhausted('Authorization requires a timezone-aware timestamp') from exc


def _schema(db: Database) -> None:
    ensure_evidence_schema(db.conn)
    db.conn.executescript('''
        CREATE TABLE IF NOT EXISTS openrouter_recurring_authorization (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            authorization_id TEXT NOT NULL UNIQUE,
            config_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS openrouter_claim_authorizations (
            claim_id INTEGER PRIMARY KEY,
            authorization_id TEXT NOT NULL,
            utc_month TEXT NOT NULL
        );
        CREATE TRIGGER IF NOT EXISTS openrouter_authority_no_update
        BEFORE UPDATE ON openrouter_recurring_authorization BEGIN
            SELECT RAISE(ABORT, 'recurring authorization is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS openrouter_authority_no_delete
        BEFORE DELETE ON openrouter_recurring_authorization BEGIN
            SELECT RAISE(ABORT, 'recurring authorization is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS openrouter_assignment_no_update
        BEFORE UPDATE ON openrouter_claim_authorizations BEGIN
            SELECT RAISE(ABORT, 'claim authorization is immutable'); END;
        CREATE TRIGGER IF NOT EXISTS openrouter_assignment_no_delete
        BEFORE DELETE ON openrouter_claim_authorizations BEGIN
            SELECT RAISE(ABORT, 'claim authorization is immutable'); END;
    ''')
    # Includes later claims made through the original one-off interface. Unknown
    # timestamps cannot grant capacity: unresolved holds are charged globally.
    with db.conn:
        db.conn.execute('''INSERT OR IGNORE INTO openrouter_claim_authorizations
            SELECT id, 'legacy-one-off', COALESCE(strftime('%Y-%m', reserved_at), 'unknown')
            FROM openrouter_budget_ledger''')


def approve_authorization(db: Database, authorization: RecurringAuthorization) -> None:
    """Persist the single explicitly approved policy; reject any replacement/reset."""
    config = authorization.serialized()
    _schema(db)
    try:
        db.conn.execute('BEGIN IMMEDIATE')
        row = db.conn.execute('SELECT config_json FROM openrouter_recurring_authorization').fetchone()
        if row and row[0] != config:
            raise BudgetExhausted('Persisted recurring authorization cannot be replaced or increased')
        if row is None:
            db.conn.execute('INSERT INTO openrouter_recurring_authorization VALUES (1, ?, ?)',
                            (authorization.authorization_id, config))
        db.conn.commit()
    except Exception:
        db.conn.rollback()
        raise


class PeriodBudget(OpenRouterBudget):
    """Drop-in classifier budget retaining receipts, pricing and reconciliation.

    increment_id must identify the verified immutable metadata revision, not a
    process/job ID. Its deterministic run ID survives crashes and month rollover.
    Only approval plus an exact matching config and enabled=True permits claims.
    """

    def __init__(self, db: Database, *, authorization: RecurringAuthorization,
                 increment_id: str, enabled: bool = False,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.db, self.conn = db, db.conn
        self.authorization = authorization
        self.clock = clock
        self.enabled = enabled
        if not increment_id.strip():
            raise BudgetExhausted('An immutable metadata increment ID is required')
        self.run_id = 'recurring-' + hashlib.sha256(json.dumps(
            [authorization.authorization_id, increment_id]).encode()).hexdigest()
        _schema(db)
        self._matching_authority()

    def _matching_authority(self) -> None:
        config = self.authorization.serialized()
        row = self.conn.execute('SELECT config_json FROM openrouter_recurring_authorization').fetchone()
        if not self.enabled or row is None or row[0] != config:
            raise BudgetExhausted('Recurring inference disabled: explicit matching authorization required')

    def _check_capacity(self, reserved: float, now: str) -> None:
        self._matching_authority()
        instant = self.clock()
        if instant.tzinfo is None:
            raise BudgetExhausted('Budget clock must be timezone-aware')
        instant = instant.astimezone(UTC)
        if instant < _utc(self.authorization.effective_at):
            raise BudgetExhausted('Recurring authorization is not effective yet')
        self._claim_month = instant.strftime('%Y-%m')
        # Each row appears exactly once. All unresolved holds (including legacy
        # and prior months) consume this month's capacity until exact reconciliation.
        monthly, run = self.conn.execute('''SELECT
            COALESCE(SUM(CASE WHEN l.status IN ('reserved','uncertain','accounting_breach')
                THEN l.reserved_usd
                WHEN a.authorization_id=? AND a.utc_month=?
                THEN COALESCE(l.actual_usd,l.reserved_usd) ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN l.run_id=? THEN
                CASE WHEN l.status='completed' THEN COALESCE(l.actual_usd,l.reserved_usd)
                ELSE l.reserved_usd END ELSE 0 END),0)
            FROM openrouter_budget_ledger l
            LEFT JOIN openrouter_claim_authorizations a ON a.claim_id=l.id
            WHERE l.status IN ('reserved','uncertain','completed','accounting_breach')''',
            (self.authorization.authorization_id, self._claim_month, self.run_id)).fetchone()
        if monthly + reserved > self.authorization.monthly_usd:
            raise BudgetExhausted('Recurring UTC monthly budget exhausted (including unresolved holds)')
        if run + reserved > self.authorization.run_usd:
            raise BudgetExhausted('Recurring increment run budget exhausted')

    def _claim_created(self, claim_id: int, now: str) -> None:
        self.conn.execute('INSERT INTO openrouter_claim_authorizations VALUES (?, ?, ?)',
                          (claim_id, self.authorization.authorization_id, self._claim_month))
