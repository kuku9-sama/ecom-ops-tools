from pathlib import Path

from ecommerce.adapters.mock_extractor import MockExtractor
from ecommerce.repositories.sqlite import ExtractionRepository
from ecommerce.services.extraction_service import ExtractionService, read_jsonl

DATA = Path(__file__).resolve().parents[1] / "data"


def test_mock_extract_normal():
    result = ExtractionService(MockExtractor()).extract_one("27 inch monitor")

    assert result.ok
    assert result.attempts == 1
    assert result.draft is not None
    assert result.draft.currency == "CNY"


def test_repair_then_success():
    result = ExtractionService(MockExtractor()).extract_one("REPAIR example: hub")

    assert result.ok
    assert result.attempts == 2


def test_broken_records_failure(db):
    repo = ExtractionRepository(db)
    service = ExtractionService(MockExtractor(), repo)

    with db:
        result = service.extract_one("BROKEN example: unknown")

    assert not result.ok
    assert repo.count_failures() == 1


def test_read_jsonl():
    assert len(read_jsonl(DATA / "raw_products.jsonl")) == 4
