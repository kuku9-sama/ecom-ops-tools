import sqlite3

import pytest

from ecommerce.repositories.sqlite import ReconciliationRepository


def test_upsert_status_and_load(db):
    repo = ReconciliationRepository(db)

    with db:
        repo.upsert_status("lazada", "O1", "L1", "amount_mismatch", "handled", "已确认")

    assert repo.load_statuses() == {
        ("lazada", "O1", "L1", "amount_mismatch"): {
            "status": "handled",
            "note": "已确认",
        }
    }


def test_upsert_updates_existing_key(db):
    repo = ReconciliationRepository(db)
    key = ("lazada", "O1", "L1", "amount_mismatch")

    with db:
        repo.upsert_status(*key, "handled", "first")
        repo.upsert_status(*key, "ignored", "second")

    loaded = repo.load_statuses()
    assert len(loaded) == 1
    assert loaded[key] == {"status": "ignored", "note": "second"}


def test_load_statuses_empty(db):
    assert ReconciliationRepository(db).load_statuses() == {}


def test_rejects_unknown_status(db):
    repo = ReconciliationRepository(db)

    with pytest.raises(sqlite3.IntegrityError):
        with db:
            repo.upsert_status(
                "lazada", "O1", "L1", "amount_mismatch", "done", ""
            )
