from pathlib import Path

from ecommerce.adapters.shopee_mock import ShopeeMockAdapter
from ecommerce.domain.errors import TransientError, ValidationError
from ecommerce.repositories.sqlite import ProductRepository
from ecommerce.services.product_service import (
    ProductService,
    idempotency_key,
    read_products_csv,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "products.csv"


def test_read_products_csv():
    items, issues = read_products_csv(DATA)

    assert len(items) == 1
    assert len(issues) == 4
    assert items[0].draft.sku == "MON-001"
    assert items[0].draft.price_minor == 129900


def test_import_products_is_idempotent(db):
    items, _ = read_products_csv(DATA)
    repo = ProductRepository(db)
    service = ProductService(repo, ShopeeMockAdapter())

    with db:
        first = service.import_drafts(items)
    assert [r.status for r in first] == ["created"]

    with db:
        second = service.import_drafts(items)
    assert [r.status for r in second] == ["exists"]

    assert repo.count_products() == 1


def test_local_cache_makes_second_process_run_exists(db):
    items, _ = read_products_csv(DATA)
    repo = ProductRepository(db)

    with db:
        first = ProductService(repo, ShopeeMockAdapter()).import_drafts(items)
    with db:
        second = ProductService(repo, ShopeeMockAdapter()).import_drafts(items)

    assert first[0].status == "created"
    assert second[0].status == "exists"
    assert repo.count_products() == 1


def test_partial_failure_does_not_rollback_other_rows(tmp_path, db):
    csv_path = tmp_path / "products.csv"
    csv_path.write_text(
        "sku,title,price,currency,stock,category,image_url\n"
        "GOOD-001,Good product,10.00,CNY,5,Test,https://example.com/good.jpg\n"
        "BAD-001,Bad product,20.00,CNY,5,Test,https://example.com/bad.jpg\n",
        encoding="utf-8",
    )

    class FailingAdapter(ShopeeMockAdapter):
        def create_product_draft(self, draft, idempotency_key):
            if draft.sku == "BAD-001":
                raise ValidationError("platform rejected")
            return super().create_product_draft(draft, idempotency_key)

    items, _ = read_products_csv(csv_path)
    repo = ProductRepository(db)
    service = ProductService(repo, FailingAdapter())

    with db:
        results = service.import_drafts(items)

    assert sorted(r.status for r in results) == ["created", "failed"]
    assert repo.count_products() == 1


def test_timeout_then_find_avoids_duplicate(tmp_path, db):
    csv_path = tmp_path / "products.csv"
    csv_path.write_text(
        "sku,title,price,currency,stock,category,image_url\n"
        "T-001,Timeout product,10.00,CNY,5,Test,https://example.com/t.jpg\n",
        encoding="utf-8",
    )

    class TimeoutAdapter(ShopeeMockAdapter):
        def __init__(self):
            super().__init__()
            self.create_calls = 0

        def create_product_draft(self, draft, idempotency_key):
            self.create_calls += 1
            self._drafts[draft.sku] = "draft-created-before-timeout"
            raise TransientError("timeout")

    items, _ = read_products_csv(csv_path)
    adapter = TimeoutAdapter()
    repo = ProductRepository(db)
    service = ProductService(repo, adapter)

    with db:
        results = service.import_drafts(items)

    assert results[0].status == "created"
    assert results[0].platform_id == "draft-created-before-timeout"
    assert adapter.create_calls == 1
    assert repo.count_products() == 1


def test_idempotency_key_depends_on_platform_and_sku():
    assert idempotency_key("lazada", "X") == idempotency_key("lazada", "X")
    assert idempotency_key("lazada", "X") != idempotency_key("shopee_mock", "X")
