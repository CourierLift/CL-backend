"""Bounded decoding and privacy normalization for NEW delivery proof photos.

Existing stored proofs are deliberately never decoded/re-encoded on retrieval.
Their recorded SHA256 continues to describe their original stored bytes.
"""

from dataclasses import dataclass
from io import BytesIO
import struct
import warnings

from PIL import Image, ImageFile, ImageOps, PngImagePlugin, UnidentifiedImageError


MAX_RAW_BYTES = 10 * 1024 * 1024
MAX_OUTPUT_BYTES = 4_000_000
MAX_INPUT_PIXELS = 16_000_000
MAX_INPUT_DIMENSION = 8192
MAX_OUTPUT_DIMENSION = 2048
JPEG_QUALITY = 85
FORMAT_CONTENT_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
GENERIC_CONTENT_TYPES = {"", "application/octet-stream"}
HEIF_CONTENT_TYPES = {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"}
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}

# The service only decodes allowlisted formats. These conservative process-wide
# Pillow limits also bound compressed PNG metadata before pixels are loaded.
Image.MAX_IMAGE_PIXELS = MAX_INPUT_PIXELS
ImageFile.LOAD_TRUNCATED_IMAGES = False
PngImagePlugin.MAX_TEXT_CHUNK = 64 * 1024
PngImagePlugin.MAX_TEXT_MEMORY = 256 * 1024

HEIF_MESSAGE = (
    "HEIC/HEIF photos are not supported. Choose a JPEG, PNG, or WebP export. "
    "For new iPhone photos, use Camera > Formats > Most Compatible."
)
DIMENSION_MESSAGE = (
    "This photo is too large to process. Choose a photo up to 16 megapixels "
    "and 8192 pixels per side, or export a smaller copy."
)
INVALID_MESSAGE = "This photo is damaged or unreadable. Choose a new JPEG, PNG, or WebP photo."


class ProofImageError(ValueError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class NormalizedProof:
    data: bytes
    width: int
    height: int
    content_type: str = "image/jpeg"
    extension: str = ".jpg"


class _BoundedOutput(BytesIO):
    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > self.limit:
            raise ProofImageError(413, "The saved photo would be too large. Choose a smaller photo.")
        return super().write(data)


def declared_content_type(value: str | None) -> str:
    """An absent Safari MIME is acceptable only after the byte decoder succeeds."""
    content_type = (value or "").split(";", 1)[0].strip().lower()
    if content_type in HEIF_CONTENT_TYPES:
        raise ProofImageError(415, HEIF_MESSAGE)
    if content_type not in {*FORMAT_CONTENT_TYPES.values(), *GENERIC_CONTENT_TYPES}:
        raise ProofImageError(415, "Proof must be a JPEG, PNG, or WebP photo.")
    return content_type


def _check_size(image: Image.Image) -> None:
    width, height = image.size
    if (width < 1 or height < 1 or max(width, height) > MAX_INPUT_DIMENSION
            or width * height > MAX_INPUT_PIXELS):
        raise ProofImageError(413, DIMENSION_MESSAGE)


def _is_heif(data: bytes) -> bool:
    # Recognition only: no HEIF decoder/plugin is installed or called. Read just
    # the bounded ftyp box, not arbitrary payload bytes or a filename extension.
    if len(data) < 16 or data[4:8] != b"ftyp":
        return False
    length = int.from_bytes(data[:4], "big")
    end = min(max(length, 16), len(data), 256)
    brands = {data[8:12], *(data[index:index + 4] for index in range(16, end, 4))}
    return bool(brands & HEIF_BRANDS)


def _has_complete_png_end(data: bytes) -> bool:
    # Pillow verifies pixel/chunk data but can accept a missing IEND CRC. Walk
    # only bounded chunk lengths and require the complete, empty terminal chunk.
    # Extra bytes after IEND are harmless here because output is newly encoded.
    offset = 8
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset:offset + 4], "big")
        end = offset + length + 12
        if end > len(data):
            return False
        if data[offset + 4:offset + 8] == b"IEND":
            return length == 0 and data[offset + 8:end] == b"\xae\x42\x60\x82"
        offset = end
    return False


def normalize_proof(data: bytes, content_type: str | None, *,
                    max_input_bytes: int = MAX_RAW_BYTES,
                    max_output_bytes: int = MAX_OUTPUT_BYTES) -> NormalizedProof:
    """Decode one genuine static image; save only upright bounded RGB pixels.

    A fresh RGB image prevents EXIF/GPS/XMP/ICC/comments/text and trailing input
    payloads being carried into the output. JPEG conversion is intentionally
    lossy; transparency is composited on white. The caller hashes these returned
    bytes, not the selected original file, and preserves them for retrieval.
    """
    declared = declared_content_type(content_type)
    if not data:
        raise ProofImageError(422, "Proof file is empty.")
    if len(data) > min(max_input_bytes, MAX_RAW_BYTES):
        raise ProofImageError(413, "Proof file exceeds the configured size limit.")
    if _is_heif(data):
        raise ProofImageError(415, HEIF_MESSAGE)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            # Probe and verify before a separate full pixel decode. The allowlist
            # avoids initializing decoders for TIFF/PDF/GIF/AVIF/etc.
            with Image.open(BytesIO(data), formats=tuple(FORMAT_CONTENT_TYPES)) as probe:
                _check_size(probe)
                actual_type = FORMAT_CONTENT_TYPES.get(probe.format)
                if actual_type is None:
                    raise ProofImageError(415, "Proof must be a JPEG, PNG, or WebP photo.")
                if declared not in GENERIC_CONTENT_TYPES and declared != actual_type:
                    raise ProofImageError(415, "The photo contents do not match its file type. Export a new JPEG, PNG, or WebP copy.")
                if probe.format == "PNG" and not _has_complete_png_end(data):
                    raise ProofImageError(422, INVALID_MESSAGE)
                if getattr(probe, "n_frames", 1) != 1:
                    raise ProofImageError(422, "Animated or multiple-frame photos are not supported. Choose one still photo.")
                probe.verify()

            with Image.open(BytesIO(data), formats=tuple(FORMAT_CONTENT_TYPES)) as source:
                _check_size(source)
                source.load()  # Reject truncated pixel streams; never allow partial decode.
                orientation = source.getexif().get(274, 1)
                if type(orientation) is not int or orientation not in range(1, 9):
                    raise ProofImageError(422, "The photo orientation is invalid. Export a new JPEG, PNG, or WebP copy.")
                ImageOps.exif_transpose(source, in_place=True)
                source.thumbnail((MAX_OUTPUT_DIMENSION, MAX_OUTPUT_DIMENSION), Image.Resampling.LANCZOS,
                                 reducing_gap=3.0)
                with Image.new("RGB", source.size, "white") as clean:
                    if source.mode in {"RGBA", "LA"} or "transparency" in source.info:
                        with source.convert("RGBA") as rgba:
                            with rgba.getchannel("A") as alpha:
                                clean.paste(rgba, (0, 0), alpha)
                    else:
                        with source.convert("RGB") as rgb:
                            clean.paste(rgb)
                    output = _BoundedOutput(min(max_output_bytes, MAX_OUTPUT_BYTES, max_input_bytes))
                    try:
                        clean.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True,
                                   progressive=False, subsampling=2)
                        return NormalizedProof(output.getvalue(), clean.width, clean.height)
                    finally:
                        output.close()
    except ProofImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ProofImageError(413, DIMENSION_MESSAGE) from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, struct.error) as error:
        raise ProofImageError(422, INVALID_MESSAGE) from error
