"""
Unit tests for the new adapter stubs:
  - adapters/rent_comps.py
  - adapters/payments.py
  - adapters/accounting_export.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── rent_comps ────────────────────────────────────────────────────────────────

class TestRentCompsAdapter:
    def test_manual_entry_returns_result(self):
        from adapters.rent_comps import manual_entry, RentCompsResult
        r = manual_entry(market_rent=1_400, comp_count=4, confidence="high",
                         notes="Zillow comps 46201")
        assert isinstance(r, RentCompsResult)
        assert r.market_rent == 1_400
        assert r.comp_count  == 4
        assert r.confidence  == "high"
        assert r.source      == "manual"

    def test_manual_entry_zero_market_rent_raises(self):
        from adapters.rent_comps import manual_entry
        with pytest.raises(ValueError, match="positive"):
            manual_entry(market_rent=0.0)

    def test_manual_entry_negative_raises(self):
        from adapters.rent_comps import manual_entry
        with pytest.raises(ValueError):
            manual_entry(market_rent=-100)

    def test_invalid_confidence_raises(self):
        from adapters.rent_comps import RentCompsResult
        with pytest.raises(ValueError, match="confidence"):
            RentCompsResult(market_rent=1_200, comp_count=3, confidence="very_high")

    def test_fetch_dry_run_returns_stub(self):
        from adapters.rent_comps import fetch_rent_comps
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.rent_comps as rc
            importlib.reload(rc)
            r = rc.fetch_rent_comps("123 Main", "Indianapolis", "IN", 3, 1.0)
            assert r.market_rent == 0.0
            assert r.confidence  == "low"
            assert r.source      == "dry_run_stub"

    def test_fetch_live_raises(self):
        from adapters.rent_comps import fetch_rent_comps
        with patch.dict("os.environ", {"SYSTEM_MODE": "live"}):
            import importlib, adapters.rent_comps as rc
            importlib.reload(rc)
            with pytest.raises(NotImplementedError):
                rc.fetch_rent_comps("123 Main", "Indianapolis", "IN", 3, 1.0)

    def test_manual_entry_default_confidence(self):
        from adapters.rent_comps import manual_entry
        r = manual_entry(market_rent=1_200)
        assert r.confidence == "medium"
        assert r.comp_count == 0

    def test_manual_entry_with_comps_list(self):
        from adapters.rent_comps import manual_entry
        comps = [{"address": "100 Oak St", "rent": 1_350}]
        r = manual_entry(market_rent=1_350, comp_count=1, confidence="medium", comps=comps)
        assert len(r.comps) == 1
        assert r.comps[0]["rent"] == 1_350


# ── payments ─────────────────────────────────────────────────────────────────

class TestPaymentsAdapter:
    def test_submit_dry_run(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.payments as pm
            importlib.reload(pm)
            result = pm.submit_payment(
                payment_type    = "emd",
                amount          = 1_000.0,
                deal_id         = 42,
                payee_name      = "Chicago Title",
                payee_account   = "routing=123 acct=456",
                memo            = "EMD deal 42",
                idempotency_key = "deal42-emd-001",
            )
            assert result.status      == "dry_run"
            assert result.amount      == 1_000.0
            assert result.deal_id     == 42
            assert result.payment_type == "emd"

    def test_submit_live_raises(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "live"}):
            import importlib, adapters.payments as pm
            importlib.reload(pm)
            with pytest.raises(NotImplementedError):
                pm.submit_payment(
                    payment_type="emd", amount=500.0, deal_id=1,
                    payee_name="Test", payee_account="x",
                    memo="test", idempotency_key="k",
                )

    def test_invalid_payment_type_raises(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.payments as pm
            importlib.reload(pm)
            with pytest.raises(ValueError, match="payment_type"):
                pm.submit_payment(
                    payment_type="bribe", amount=500.0, deal_id=1,
                    payee_name="Test", payee_account="x",
                    memo="test", idempotency_key="k",
                )

    def test_zero_amount_raises(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.payments as pm
            importlib.reload(pm)
            with pytest.raises(ValueError, match="positive"):
                pm.submit_payment(
                    payment_type="emd", amount=0.0, deal_id=1,
                    payee_name="Test", payee_account="x",
                    memo="test", idempotency_key="k",
                )

    def test_check_status_dry_run(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.payments as pm
            importlib.reload(pm)
            s = pm.check_payment_status("dry_run_deal42-emd-001")
            assert s.status == "dry_run"

    def test_all_payment_types_accepted(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.payments as pm
            importlib.reload(pm)
            for ptype in pm.PAYMENT_TYPES:
                r = pm.submit_payment(
                    payment_type=ptype, amount=100.0, deal_id=1,
                    payee_name="Test", payee_account="x",
                    memo="test", idempotency_key=f"k-{ptype}",
                )
                assert r.status == "dry_run"


# ── accounting_export ──────────────────────────────────────────────────────────

class TestAccountingExportAdapter:
    def test_export_transaction_dry_run(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.accounting_export as ae
            importlib.reload(ae)
            r = ae.export_transaction(
                deal_id          = 7,
                transaction_type = "assignment_income",
                amount           = 10_500.0,
                transaction_date = "2026-10-01",
                memo             = "Assignment fee deal 7",
                idempotency_key  = "deal7-assign-001",
            )
            assert r.status           == "dry_run"
            assert r.deal_id          == 7
            assert r.amount           == 10_500.0
            assert r.transaction_type == "assignment_income"

    def test_export_transaction_live_raises(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "live"}):
            import importlib, adapters.accounting_export as ae
            importlib.reload(ae)
            with pytest.raises(NotImplementedError):
                ae.export_transaction(
                    deal_id=1, transaction_type="acquisition", amount=33_000,
                    transaction_date="2026-10-01", memo="purchase",
                    idempotency_key="k",
                )

    def test_invalid_transaction_type_raises(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.accounting_export as ae
            importlib.reload(ae)
            with pytest.raises(ValueError, match="transaction_type"):
                ae.export_transaction(
                    deal_id=1, transaction_type="bribe", amount=500,
                    transaction_date="2026-10-01", memo="test", idempotency_key="k",
                )

    def test_export_rent_roll_dry_run(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.accounting_export as ae
            importlib.reload(ae)
            rows = [
                {"deal_id": 1, "address": "123 Main", "tenant_name": "J. Smith",
                 "monthly_rent": 1_200, "amount_paid": 1_200, "status": "paid"},
                {"deal_id": 2, "address": "456 Oak", "tenant_name": "A. Jones",
                 "monthly_rent": 1_400, "amount_paid": 0, "status": "late"},
            ]
            r = ae.export_rent_roll(
                period="2026-10", rent_roll_rows=rows, idempotency_key="roll-oct-26",
            )
            assert r.status == "dry_run"
            assert r.amount == 1_200.0  # only amount_paid summed

    def test_all_transaction_types_accepted(self):
        with patch.dict("os.environ", {"SYSTEM_MODE": "dry_run"}):
            import importlib, adapters.accounting_export as ae
            importlib.reload(ae)
            for ttype in ae.TRANSACTION_TYPES:
                r = ae.export_transaction(
                    deal_id=1, transaction_type=ttype, amount=100.0,
                    transaction_date="2026-10-01", memo="test",
                    idempotency_key=f"k-{ttype}",
                )
                assert r.status == "dry_run"
