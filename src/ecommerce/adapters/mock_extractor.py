import hashlib
import json
import re


class MockExtractor:
    def extract(self, text: str, repair_error: str | None = None) -> str:
        if "BROKEN" in text:
            return "not json"
        if "REPAIR" in text and repair_error is None:
            return "not json"

        sku = "EXT-" + hashlib.sha256(text.encode()).hexdigest()[:8]
        title = text.split(",")[0].strip()[:60] or "Extracted product"
        price = re.search(r"(\d+(?:\.\d{1,2})?)\s*CNY", text)
        stock = re.search(r"stock\s+(\d+)", text)
        return json.dumps(
            {
                "sku": sku,
                "title": title,
                "price": price.group(1) if price else "0.00",
                "currency": "CNY",
                "stock": int(stock.group(1)) if stock else 0,
                "category": "General",
                "image_url": "https://example.com/ext.jpg",
            },
            ensure_ascii=False,
        )
