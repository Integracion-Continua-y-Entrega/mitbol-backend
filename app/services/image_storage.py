"""Imágenes procesadas y diario local para coordinar archivos con PostgreSQL."""

from dataclasses import dataclass, field
from io import BytesIO
import json
import os
from pathlib import Path
import re
from uuid import uuid4
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

from app.models import ProductImage
from app.services.enrichment_errors import EnrichmentError, StorageError

MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_URL_PATTERN = re.compile(
    r"/media/products/([1-9][0-9]*)/variants/([1-9][0-9]*)/([0-9a-f]{32}\.(jpg|png|webp))"
)


def validate_image(data: bytes) -> tuple[bytes, str]:
    if len(data) > MAX_IMAGE_BYTES:
        raise EnrichmentError(413, "La imagen supera el límite de 5 MB (5,000,000 bytes).")
    if not data:
        raise EnrichmentError(422, "La imagen está vacía.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                format_name = source.format
                if format_name not in {"JPEG", "PNG", "WEBP"}:
                    raise EnrichmentError(415, "Sólo se admiten imágenes JPEG, PNG y WebP.")
                if source.width * source.height > MAX_IMAGE_PIXELS or getattr(source, "n_frames", 1) != 1:
                    raise EnrichmentError(422, "La imagen debe ser estática y tener como máximo 20 megapíxeles.")
                source.verify()
            with Image.open(BytesIO(data)) as source:
                source.load()
                image = ImageOps.exif_transpose(source)
                image = image.convert("RGB" if format_name == "JPEG" else "RGBA")
                image.info.clear()
                output = BytesIO()
                image.save(output, format=format_name, **({"quality": 90} if format_name != "PNG" else {}))
                processed = output.getvalue()
                if len(processed) > MAX_IMAGE_BYTES:
                    raise EnrichmentError(413, "La imagen procesada supera el límite de 5 MB.")
                return processed, {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}[format_name]
    except EnrichmentError:
        raise
    except UnidentifiedImageError:
        recognized = data.startswith((b"\xff\xd8", b"\x89PNG")) or data[:4] == b"RIFF"
        raise EnrichmentError(422 if recognized else 415, "El archivo no contiene una imagen válida admitida.") from None
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise EnrichmentError(422, "La imagen está dañada o excede los límites admitidos.") from None


@dataclass(frozen=True)
class FileOperation:
    kind: str
    image_url: str
    operation_id: str = field(default_factory=lambda: uuid4().hex)


class LocalImageStorage:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _inside(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts)
        if not path.resolve().is_relative_to(self.root):
            raise StorageError()
        return path

    def path_for(self, image_url: str) -> Path:
        match = IMAGE_URL_PATTERN.fullmatch(image_url)
        if match is None:
            raise StorageError()
        product, variant, filename, _ = match.groups()
        return self._inside(product, "variants", variant, filename)

    def _paths(self, operation: FileOperation):
        final = self.path_for(operation.image_url)
        if not re.fullmatch(r"[0-9a-f]{32}", operation.operation_id):
            raise StorageError()
        filename = operation.operation_id + final.suffix
        return (
            final, self._inside(".staging", filename), self._inside(".trash", filename),
            self._inside(".pending", operation.operation_id + ".json"),
        )

    @staticmethod
    def _write_file(path: Path, content: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def _record(self, operation: FileOperation):
        _, _, _, journal = self._paths(operation)
        temporary = journal.with_suffix(".tmp")
        self._write_file(temporary, json.dumps({
            "kind": operation.kind, "image_url": operation.image_url,
            "operation_id": operation.operation_id,
        }).encode("utf-8"))
        os.replace(temporary, journal)

    def prepare_create(self, content: bytes, extension: str, product_id: int, variant_id: int) -> FileOperation:
        if extension not in {"jpg", "png", "webp"}:
            raise StorageError()
        operation = FileOperation("create", f"/media/products/{product_id}/variants/{variant_id}/{uuid4().hex}.{extension}")
        final, staging, _, _ = self._paths(operation)
        self._record(operation)  # El diario se escribe antes de crear el archivo.
        self._write_file(staging, content)
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
        return operation

    def prepare_delete(self, image_url: str) -> FileOperation:
        operation = FileOperation("delete", image_url)
        final, _, trash, _ = self._paths(operation)
        self._record(operation)
        if final.exists():
            trash.parent.mkdir(parents=True, exist_ok=True)
            os.replace(final, trash)
        return operation

    def settle(self, operation: FileOperation, referenced: bool):
        final, staging, trash, journal = self._paths(operation)
        if referenced:
            if not final.is_file():
                recoverable = staging if operation.kind == "create" else trash
                if not recoverable.is_file():
                    raise StorageError()
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(recoverable, final)
        else:
            final.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)
        trash.unlink(missing_ok=True)
        journal.unlink(missing_ok=True)

    def recover(self, db):
        """Invocar sólo bajo el bloqueo PostgreSQL de operaciones de imágenes."""
        pending = self._inside(".pending")
        if not pending.exists():
            return
        for journal in sorted(pending.glob("*.json")):
            try:
                payload = json.loads(journal.read_text(encoding="utf-8"))
                operation = FileOperation(**payload)
                if operation.kind not in {"create", "delete"} or self._paths(operation)[3] != journal:
                    raise StorageError()
            except (TypeError, ValueError):
                raise StorageError() from None
            referenced = db.scalar(select(ProductImage.product_image_id).where(
                ProductImage.image_url == operation.image_url,
            ).limit(1)) is not None
            self.settle(operation, referenced)
        # Un .tmp de diario nunca precede a un archivo de imagen: es seguro retirarlo.
        for temporary in pending.glob("*.tmp"):
            if re.fullmatch(r"[0-9a-f]{32}\.tmp", temporary.name):
                self._inside(".pending", temporary.name).unlink(missing_ok=True)
