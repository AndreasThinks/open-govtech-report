from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from govtech_scraper.db import Database
from govtech_scraper.decision.evidence import BudgetExhausted, DEFAULT_MODEL, OpenRouterBudget
from govtech_scraper.decision.period_budget import RecurringAuthorization, PeriodBudget, approve_authorization

AUTH = RecurringAuthorization('ongoing', 5.0, 2.0, '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')
OCT = datetime(2026, 10, 31, 23, 59, tzinfo=UTC)
NOV = datetime(2026, 11, 1, tzinfo=UTC)


def budget(db, increment='revision1', now=OCT, enabled=True, authorization=AUTH):
    return PeriodBudget(db, authorization=authorization, increment_id=increment,
                        enabled=enabled, clock=lambda: now)


def claim(b, key, dollars=1):
    return b.reserve(model=DEFAULT_MODEL, request_hash=key, estimated_input_tokens=0,
                     max_output_tokens=int(dollars / 0.0000005))


def test_default_disabled_and_immutable_approval(tmp_db):
    with pytest.raises(BudgetExhausted):
        budget(tmp_db)
    approve_authorization(tmp_db, AUTH)
    with pytest.raises(BudgetExhausted):
        budget(tmp_db, enabled=False)
    for changed in [replace(AUTH, authorization_id='new'), replace(AUTH, monthly_usd=6),
                    replace(AUTH, run_usd=3), replace(AUTH, effective_at='2026-11-01T00:00:00+00:00')]:
        with pytest.raises(BudgetExhausted):
            approve_authorization(tmp_db, changed)
        with pytest.raises(BudgetExhausted):
            budget(tmp_db, authorization=changed)


def test_restart_run_cap_and_month_cap(tmp_db):
    approve_authorization(tmp_db, AUTH)
    b = budget(tmp_db)
    b.complete(claim(b, 'a', 2).id, {'total_cost': 2})
    assert budget(tmp_db).run_id == b.run_id
    with pytest.raises(BudgetExhausted):
        claim(budget(tmp_db), 'b', .01)
    b2 = budget(tmp_db, 'revision2')
    b2.complete(claim(b2, 'c', 2).id, {'total_cost': 2})
    b3 = budget(tmp_db, 'revision3')
    claim(b3, 'd', 1)
    with pytest.raises(BudgetExhausted):
        claim(budget(tmp_db, 'arbitrary-new-id'), 'e', .01)
    # A new month replenishes monthly capacity but never a resumed run's cap.
    with pytest.raises(BudgetExhausted):
        claim(budget(tmp_db, now=NOV), 'f', .01)
    claim(budget(tmp_db, 'nov', NOV), 'g', 2)


def test_legacy_ledger_and_old_holds_are_preserved(tmp_db):
    old = OpenRouterBudget(tmp_db, ceiling_usd=50, run_id='historical')
    old.complete(claim(old, 'spent', 20).id, {'total_cost': 20})
    held = claim(old, 'unknown', 4)
    old.mark_uncertain(held.id, 'unknown generation')
    before = [tuple(r) for r in tmp_db.conn.execute('SELECT * FROM openrouter_budget_ledger')]
    approve_authorization(tmp_db, AUTH)
    b = budget(tmp_db, now=NOV)
    claim(b, 'new', 1)
    with pytest.raises(BudgetExhausted):
        claim(budget(tmp_db, 'another', NOV), 'over', .01)
    assert before == [tuple(r) for r in tmp_db.conn.execute('SELECT * FROM openrouter_budget_ledger WHERE id<=2')]
    assert tmp_db.conn.execute("SELECT value FROM openrouter_budget_config WHERE key='ceiling_usd'").fetchone()[0] == '50.0'
    assert tmp_db.conn.execute('SELECT authorization_id FROM openrouter_claim_authorizations WHERE claim_id=?', (held.id,)).fetchone()[0] == 'legacy-one-off'


def test_cross_month_hold_counted_once(tmp_db):
    approve_authorization(tmp_db, AUTH)
    claim(budget(tmp_db), 'pending', 2)
    b = budget(tmp_db, 'next', NOV)
    b.complete(claim(b, 'next', 2).id, {'total_cost': 2})
    claim(budget(tmp_db, 'third', NOV), 'third', 1)
    with pytest.raises(BudgetExhausted):
        claim(budget(tmp_db, 'fourth', NOV), 'fourth', .01)


def test_concurrent_reservations_are_atomic(tmp_path):
    path = str(tmp_path / 'ledger.db')
    db = Database(path)
    approve_authorization(db, AUTH)
    db.close()
    def reserve(i):
        db = Database(path)
        try:
            claim(budget(db), str(i), 1.25)
            return True
        except BudgetExhausted:
            return False
        finally:
            db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, range(2))) == [False, True]


def test_recurring_spend_does_not_change_legacy_authority_capacity(tmp_db):
    old = OpenRouterBudget(tmp_db, ceiling_usd=50, run_id='historical')
    old.complete(claim(old, 'old-paid', 49).id, {'total_cost': 49})
    approve_authorization(tmp_db, AUTH)
    recurring = budget(tmp_db)
    recurring.complete(claim(recurring, 'recurring-paid', 2).id, {'total_cost': 2})
    late = claim(old, 'legacy-final', 1)
    assert tmp_db.conn.execute('SELECT authorization_id FROM openrouter_claim_authorizations WHERE claim_id=?', (late.id,)).fetchone()[0] == 'legacy-one-off'


def test_effective_time_and_sql_immutability(tmp_db):
    import sqlite3
    approve_authorization(tmp_db, AUTH)
    with pytest.raises(BudgetExhausted):
        claim(budget(tmp_db, now=datetime(2026, 9, 30, tzinfo=UTC)), 'early')
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.conn.execute("DELETE FROM openrouter_recurring_authorization")
    tmp_db.conn.rollback()
    claim(budget(tmp_db), 'bound')
    with pytest.raises(sqlite3.IntegrityError):
        tmp_db.conn.execute("UPDATE openrouter_claim_authorizations SET utc_month='2099-01'")
    tmp_db.conn.rollback()


def test_utc_assignment_reconciliation_and_global_breach(tmp_db):
    import asyncio
    from datetime import timedelta, timezone
    from govtech_scraper.decision.evidence import AccountingBreach
    from govtech_scraper.decision.reconcile import GenerationReconciler, ReconcileResult
    OpenRouterBudget(tmp_db, ceiling_usd=50, run_id='legacy')
    approve_authorization(tmp_db, AUTH)
    local_november = datetime(2026, 11, 1, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    b = budget(tmp_db, now=local_november)
    held = claim(b, 'receipt', 2)
    b.mark_uncertain(held.id, 'missing usage')
    b.save_response_receipt(claim_id=held.id, request_hash='receipt', model=DEFAULT_MODEL,
                            response={'id': 'gen-exact'}, usage={})
    assert tmp_db.conn.execute('SELECT utc_month FROM openrouter_claim_authorizations WHERE claim_id=?', (held.id,)).fetchone()[0] == '2026-10'
    class Lookup:
        async def get_generation(self, generation_id, **kwargs):
            assert generation_id == 'gen-exact'
            return {'status': 200, 'data': {'id': generation_id, 'total_cost': 1.5}}
    reconciler = GenerationReconciler(tmp_db, api_key='fake', transport=Lookup())
    assert asyncio.run(reconciler.reconcile(request_hash='receipt')) == ReconcileResult.RECONCILED
    # Exact settlement lowers the old reservation; it doesn't reassign the claim.
    assert tmp_db.conn.execute('SELECT utc_month FROM openrouter_claim_authorizations WHERE claim_id=?', (held.id,)).fetchone()[0] == '2026-10'
    current = budget(tmp_db, 'new-month', NOV)
    broken = claim(current, 'breach', 1)
    with pytest.raises(AccountingBreach):
        current.complete(broken.id, {'total_cost': 1.1})
    with pytest.raises(AccountingBreach):
        claim(budget(tmp_db, 'other', NOV), 'blocked', .01)


def test_approval_does_not_enable_default_budget(tmp_db):
    approve_authorization(tmp_db, AUTH)
    with pytest.raises(BudgetExhausted):
        PeriodBudget(tmp_db, authorization=AUTH, increment_id='revision')


def test_restart_from_disk_retains_claim_assignment_and_duplicate_guard(tmp_path):
    from govtech_scraper.decision.evidence import PendingRequest
    path = str(tmp_path / 'restart.db')
    db = Database(path)
    approve_authorization(db, AUTH)
    first = budget(db)
    held = claim(first, 'durable', 2)
    run_id = first.run_id
    db.close()
    restored = Database(path)
    restarted = budget(restored)
    assert restarted.run_id == run_id
    pending = restarted.reserve_or_pending(model=DEFAULT_MODEL, request_hash='durable',
        estimated_input_tokens=0, max_output_tokens=100)
    assert isinstance(pending, PendingRequest) and pending.claim_id == held.id
    with pytest.raises(BudgetExhausted):
        claim(restarted, 'replacement', .01)
    assert restored.conn.execute('SELECT COUNT(*) FROM openrouter_claim_authorizations').fetchone()[0] == 1
    restored.close()
