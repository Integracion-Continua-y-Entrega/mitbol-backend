"""Contratos de HU-005 y validación de texto plano."""

from datetime import datetime
from html.parser import HTMLParser
from unicodedata import category

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.tag import Tag


class _PlainTextParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        raise ValueError("Se requiere texto plano, sin HTML.")

    def handle_endtag(self, tag):
        raise ValueError("Se requiere texto plano, sin HTML.")

    def handle_comment(self, data):
        raise ValueError("Se requiere texto plano, sin HTML.")

    def handle_decl(self, decl):
        raise ValueError("Se requiere texto plano, sin HTML.")


def validate_plain_text(value: str) -> str:
    if any(category(char) == "Cc" and char not in "\n\r\t" for char in value):
        raise ValueError("El texto contiene caracteres de control no admitidos.")
    parser = _PlainTextParser(convert_charrefs=False)
    parser.feed(value)
    parser.close()
    return value


class AdminInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DescriptionUpdate(AdminInput):
    description: str | None = Field(max_length=2000)

    @field_validator("description")
    @classmethod
    def plain_description(cls, value):
        if value is None:
            return None
        return validate_plain_text(value).strip() or None


class DescriptionResponse(BaseModel):
    product_id: int
    description: str | None

    model_config = ConfigDict(from_attributes=True)


class TagCreate(AdminInput):
    name: str

    @field_validator("name")
    @classmethod
    def canonical_name(cls, value):
        return Tag(name=validate_plain_text(value)).name


class TagResponse(BaseModel):
    tag_id: int
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TagListResponse(BaseModel):
    tags: list[TagResponse]


class ImageOrderUpdate(AdminInput):
    display_order: int = Field(ge=0, le=2_147_483_647)


class ImageResponse(BaseModel):
    product_image_id: int
    product_variant_id: int
    image_url: str
    display_order: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ImageListResponse(BaseModel):
    images: list[ImageResponse]
