from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProductStatusUpdate(BaseModel):
    status: Literal["active", "inactive"]


class ProductPublic(BaseModel):
    id: int
    product_name: str
    purchase_link: str
    photo_url: str
    ig_permalink: str
    created_at: str


class MediaItem(BaseModel):
    id: str
    caption: str = ""
    media_type: str
    image_url: str
    permalink: str
    timestamp: str


class HealthResponse(BaseModel):
    ok: bool = True
    service: str = "jjabtree-api"
