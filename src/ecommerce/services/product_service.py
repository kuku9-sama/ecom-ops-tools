import csv
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path

from ecommerce.domain.errors import (
    AuthError,
    EcommerceError,
    RateLimitError,
    TransientError,
    ValidationError,
)
from ecommerce.domain.models import ProductDraft
from ecommerce.domain.money import MINOR_UNITS, MoneyError, parse_amount_to_minor
from ecommerce.domain.ports import PlatformPort
from ecommerce.repositories.sqlite import ProductRepository

PRODUCT_COLUMNS = (
    "sku", "title", "price", "currency", "stock", "category", "image_url",
)


@dataclass(frozen=True, slots=True)
class ProductIssue:
    row_number: int
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedProduct:
    row_number: int
    draft: ProductDraft


@dataclass(frozen=True, slots=True)
class ProductRowResult:
    row_number: int
    sku: str
    status: str          # created / exists / failed
    platform_id: str | None
    message: str


def idempotency_key(platform: str, sku: str) -> str:
    return hashlib.sha256(f"{platform}:{sku}".encode()).hexdigest()


def read_products_csv(path: str | Path) -> tuple[list[ParsedProduct], list[ProductIssue]]:
    issues: list[ProductIssue] = []
    parsed: list[ParsedProduct] = []
    seen: set[str] = set()

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in PRODUCT_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValidationError(f"CSV 缺少列: {missing}")

        for row_number, row in enumerate(reader, start=2):
            sku = (row.get("sku") or "").strip()
            title = (row.get("title") or "").strip()
            currency = (row.get("currency") or "").strip().upper()
            category = (row.get("category") or "").strip()
            image_url = (row.get("image_url") or "").strip()

            if not sku:
                issues.append(ProductIssue(row_number, "sku", "不能为空"))
                continue
            if sku in seen:
                issues.append(ProductIssue(row_number, "sku", f"同一文件内 SKU 重复: {sku}"))
                continue
            if not 1 <= len(title) <= 200:
                issues.append(ProductIssue(row_number, "title", "长度必须在 1–200 字符"))
                continue
            if currency not in MINOR_UNITS:
                issues.append(ProductIssue(row_number, "currency", f"不支持的币种: {currency}"))
                continue

            try:
                price_minor = parse_amount_to_minor(row.get("price") or "", currency)
            except MoneyError as exc:
                issues.append(ProductIssue(row_number, "price", str(exc)))
                continue
            if price_minor <= 0:
                issues.append(ProductIssue(row_number, "price", "必须大于 0"))
                continue

            try:
                stock = int((row.get("stock") or "").strip())
            except ValueError:
                issues.append(ProductIssue(row_number, "stock", "必须是整数"))
                continue
            if stock < 0:
                issues.append(ProductIssue(row_number, "stock", "不能为负"))
                continue

            if not image_url.startswith(("http://", "https://")):
                issues.append(
                    ProductIssue(row_number, "image_url", "必须是 http:// 或 https:// URL")
                )
                continue

            seen.add(sku)
            parsed.append(
                ParsedProduct(
                    row_number=row_number,
                    draft=ProductDraft(
                        sku=sku,
                        title=title,
                        price_minor=price_minor,
                        currency=currency,
                        stock=stock,
                        category=category,
                        image_url=image_url,
                    ),
                )
            )

    return parsed, issues


class ProductService:
    def __init__(self, repo: ProductRepository, platform: PlatformPort):
        self._repo = repo
        self._platform = platform

    def import_drafts(self, items: list[ParsedProduct]) -> list[ProductRowResult]:
        self._platform.authenticate()
        results: list[ProductRowResult] = []
        for item in items:
            results.append(self._import_one(item))
        return results

    def _import_one(self, item: ParsedProduct) -> ProductRowResult:
        draft = item.draft
        try:
            # 先查本地镜像，避免每次 CLI 都重新创建；本地没有才问平台。
            existing = self._repo.find_platform_id(self._platform.platform, draft.sku)
            if existing is None:
                existing = self._safe_find(draft.sku)
            if existing is not None:
                self._repo.upsert(self._platform.platform, draft, existing)
                return ProductRowResult(
                    item.row_number, draft.sku, "exists", existing, "SKU 已存在"
                )

            key = idempotency_key(self._platform.platform, draft.sku)
            try:
                platform_id = self._platform.create_product_draft(draft, key)
            except (TransientError, RateLimitError) as exc:
                if isinstance(exc, RateLimitError) and exc.retry_after:
                    time.sleep(min(exc.retry_after, 8.0))
                # 服务端可能已经创建，只是响应丢了；先按 SKU 回查。
                existing = self._safe_find(draft.sku)
                if existing is not None:
                    platform_id = existing
                else:
                    platform_id = self._platform.create_product_draft(draft, key)

            self._repo.upsert(self._platform.platform, draft, platform_id)
            return ProductRowResult(item.row_number, draft.sku, "created", platform_id, "")
        except AuthError:
            raise
        except EcommerceError as exc:
            return ProductRowResult(item.row_number, draft.sku, "failed", None, str(exc))

    def _safe_find(self, sku: str) -> str | None:
        try:
            return self._platform.find_product_by_sku(sku)
        except (TransientError, RateLimitError):
            return None
