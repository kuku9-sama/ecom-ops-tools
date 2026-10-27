import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from ecommerce.domain.errors import EcommerceError, ParseError, ValidationError
from ecommerce.domain.extraction import ExtractedProduct
from ecommerce.domain.models import ProductDraft
from ecommerce.domain.money import MINOR_UNITS, parse_amount_to_minor
from ecommerce.domain.ports import ExtractionPort
from ecommerce.repositories.sqlite import ExtractionRepository


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    source_hash: str
    ok: bool
    draft: ProductDraft | None
    error: str
    attempts: int


class ExtractionService:
    def __init__(
        self, extractor: ExtractionPort, repo: ExtractionRepository | None = None
    ):
        self._extractor = extractor
        self._repo = repo

    def extract_one(self, text: str) -> ExtractionResult:
        source_hash = hashlib.sha256(text.encode()).hexdigest()
        attempts = 0
        last_raw = ""
        last_error = ""

        for repair in (False, True):
            attempts += 1
            try:
                raw = self._extractor.extract(
                    text, repair_error=last_error if repair else None
                )
                last_raw = raw
                draft = self._parse(raw)
                return ExtractionResult(source_hash, True, draft, "", attempts)
            except (EcommerceError, PydanticValidationError, ValueError) as exc:
                last_error = str(exc)

        if self._repo is not None:
            self._repo.record_failure(
                source_hash, last_raw, last_error or "提取失败"
            )
        return ExtractionResult(
            source_hash, False, None, last_error or "提取失败", attempts
        )

    def _parse(self, raw: str) -> ProductDraft:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ParseError(f"不是合法 JSON: {exc}") from exc

        product = ExtractedProduct.model_validate(data)
        sku = product.sku.strip()
        title = product.title.strip()
        if not sku or not title:
            raise ValidationError("SKU/标题不能为空")

        currency = product.currency.strip().upper()
        if currency not in MINOR_UNITS:
            raise ValidationError(f"不支持的币种: {currency}")
        price_minor = parse_amount_to_minor(product.price, currency)
        if price_minor <= 0:
            raise ValidationError("价格必须大于 0")

        image_url = product.image_url.strip()
        if not image_url.startswith(("http://", "https://")):
            raise ValidationError("image_url 必须是 http/https")

        return ProductDraft(
            sku=sku,
            title=title,
            price_minor=price_minor,
            currency=currency,
            stock=product.stock,
            category=product.category.strip(),
            image_url=image_url,
        )


def read_jsonl(path: str | Path) -> list[str]:
    texts: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"第 {line_number} 行不是 JSON") from exc
            text = data.get("text") if isinstance(data, dict) else None
            if not isinstance(text, str) or not text:
                raise ValueError(f"第 {line_number} 行缺少 text")
            texts.append(text)
    return texts
