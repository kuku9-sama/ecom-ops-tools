import hashlib
import json


class MockExtractor:
    def extract(self, text: str, repair_error: str | None = None) -> str:
        if "BROKEN" in text:
            return "not json"
        if "REPAIR" in text and repair_error is None:
            return "not json"

        sku = "EXT-" + hashlib.sha256(text.encode()).hexdigest()[:8]
        title = text.strip()[:60] or "Extracted product"
        return json.dumps(
            {
                "sku": sku,
                "title": title,
                "price": "129.90",
                "currency": "CNY",
                "stock": 10,
                "category": "General",
                "image_url": "https://example.com/ext.jpg",
            },
            ensure_ascii=False,
        )
