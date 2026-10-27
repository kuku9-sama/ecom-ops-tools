from pydantic import BaseModel, Field


class ExtractedProduct(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    price: str
    currency: str
    stock: int = Field(ge=0)
    category: str = ""
    image_url: str
