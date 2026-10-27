from openai import OpenAI

from ecommerce.domain.errors import ParseError, PermanentError

INSTRUCTION = """
从用户文本中提取商品字段，只返回 json。
字段：sku, title, price, currency, stock, category, image_url。
示例：{"sku":"SKU-1","title":"27 inch monitor","price":"129.90","currency":"CNY","stock":10,"category":"Monitors","image_url":"https://example.com/a.jpg"}
"""


class DeepSeekExtractor:
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-flash",
    ):
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置")
        self._client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        self._model = model

    def extract(self, text: str, repair_error: str | None = None) -> str:
        user_text = text
        if repair_error:
            user_text = f"{text}\n上次输出校验失败：{repair_error}\n请修复并只返回 json。"

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": INSTRUCTION},
                    {"role": "user", "content": user_text},
                ],
                response_format={"type": "json_object"},
                max_tokens=1000,
                timeout=60,
            )
        except Exception as exc:
            raise PermanentError(f"DeepSeek 调用失败: {exc}") from exc

        content = resp.choices[0].message.content
        if not content:
            raise ParseError("DeepSeek 返回空内容")
        return content
