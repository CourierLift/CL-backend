"""Real image, privacy, resource-bound, and preservation proof tests.

Every image is generated from synthetic pixels. No live object store, customer
data, credential, or network decoder is used.
"""

from hashlib import sha256
from io import BytesIO
import struct
import zlib

import pytest
from PIL import Image, PngImagePlugin

from backend.database import SessionLocal
from backend.main import app
from backend.models import DeliveryProof
from backend.routes import proofs as proof_routes
from backend.services.object_storage import LocalObjectStorage, get_object_storage
from backend.services.proof_images import (
    DIMENSION_MESSAGE, HEIF_MESSAGE, MAX_INPUT_PIXELS, MAX_RAW_BYTES,
    ProofImageError, normalize_proof,
)
from backend.settings import settings
from tests.image_helpers import photo_bytes
from tests.test_delivery_proof import create_and_claim_in_transit, submit_proof


def encoded(image, format, **options):
    with BytesIO() as output:
        image.save(output, format=format, **options)
        return output.getvalue()


def expect_error(data, content_type, status, match=None, **options):
    with pytest.raises(ProofImageError) as raised:
        normalize_proof(data, content_type, **options)
    assert raised.value.status_code == status
    if match:
        assert match in raised.value.detail


@pytest.mark.parametrize("format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
@pytest.mark.parametrize("generic", [None, "", "application/octet-stream"])
def test_genuine_supported_pixels_determine_unknown_safari_type(format, mime, generic):
    original = photo_bytes(format)
    result = normalize_proof(original, generic)
    assert result.content_type == "image/jpeg"
    assert result.extension == ".jpg"
    assert (result.width, result.height) == (32, 16)
    with Image.open(BytesIO(result.data)) as saved:
        saved.load()
        assert saved.format == "JPEG" and saved.mode == "RGB"
        assert not saved.getexif()
    assert normalize_proof(original, mime).data == result.data


@pytest.mark.parametrize("orientation,expected", [
    (1, "RGBY"), (2, "GRYB"), (3, "YBGR"), (4, "BYRG"),
    (5, "RBGY"), (6, "BRYG"), (7, "YGBR"), (8, "GYRB"),
])
def test_all_eight_exif_transforms_preserve_correct_quadrants(orientation, expected):
    colors = {"R": (240, 10, 10), "G": (10, 230, 10), "B": (10, 10, 240), "Y": (235, 235, 10)}
    with Image.new("RGB", (80, 40)) as source:
        for letter, box in zip("RGBY", [(0, 0, 40, 20), (40, 0, 80, 20), (0, 20, 40, 40), (40, 20, 80, 40)]):
            source.paste(colors[letter], box)
        exif = Image.Exif()
        exif[274] = orientation
        raw = encoded(source, "JPEG", quality=100, subsampling=0, exif=exif)
    result = normalize_proof(raw, "image/jpeg")
    with Image.open(BytesIO(result.data)) as saved:
        assert saved.size == ((40, 80) if orientation >= 5 else (80, 40))
        points = [(saved.width // 4, saved.height // 4), (3 * saved.width // 4, saved.height // 4),
                  (saved.width // 4, 3 * saved.height // 4), (3 * saved.width // 4, 3 * saved.height // 4)]
        for point, expected_color in zip(points, expected):
            pixel = saved.getpixel(point)
            assert max(abs(a - b) for a, b in zip(pixel, colors[expected_color])) < 20
        assert 274 not in saved.getexif()


@pytest.mark.parametrize("format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_metadata_and_trailing_payload_are_removed(format, mime):
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private-synthetic-description"
    exif[34853] = {1: "N", 2: (51.0, 0.0, 0.0), 3: "W", 4: (1.0, 0.0, 0.0)}
    options = {"exif": exif, "icc_profile": b"private-synthetic-icc"}
    if format == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "private-synthetic-png-text")
        metadata.add_text("XML:com.adobe.xmp", "private-synthetic-xmp")
        options["pnginfo"] = metadata
    else:
        options["xmp"] = b"private-synthetic-xmp"
    with Image.new("RGB", (32, 16), "blue") as source:
        original = encoded(source, format, **options)
    # Appended bytes have no image role and must never enter durable storage.
    result = normalize_proof(original + b"private-synthetic-trailing-payload", mime)
    with Image.open(BytesIO(result.data)) as saved:
        saved.load()
        assert saved.size == (16, 32)
        assert not saved.getexif()
        assert not ({"exif", "icc_profile", "xmp", "comment", "transparency"} & saved.info.keys())
    assert b"private-synthetic" not in result.data


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_alpha_is_composited_on_white(format, mime):
    with Image.new("RGBA", (32, 32), (255, 0, 0, 128)) as source:
        data = encoded(source, format, lossless=True) if format == "WEBP" else encoded(source, format)
    result = normalize_proof(data, mime)
    with Image.open(BytesIO(result.data)) as saved:
        assert saved.mode == "RGB"
        assert all(abs(a - b) <= 3 for a, b in zip(saved.getpixel((16, 16)), (255, 127, 127)))


def test_twelve_megapixel_iphone_photo_is_accepted_and_downscaled():
    assert 4032 * 3024 < MAX_INPUT_PIXELS
    result = normalize_proof(photo_bytes(size=(4032, 3024), orientation=6), "image/jpeg")
    assert (result.width, result.height) == (1536, 2048)
    assert len(result.data) < 4_000_000


def png_with_dimensions(width, height):
    data = bytearray(photo_bytes("PNG"))
    data[16:24] = struct.pack(">II", width, height)
    data[29:33] = struct.pack(">I", zlib.crc32(data[12:29]) & 0xFFFFFFFF)
    return bytes(data)


@pytest.mark.parametrize("size", [(8193, 1), (6000, 4000), (8000, 6000), (0, 1)])
def test_excessive_or_invalid_dimensions_fail_before_pixel_decode(size, monkeypatch):
    def forbidden_load(*args, **kwargs):
        pytest.fail("Oversized image must be refused before allocating pixel decode")
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", forbidden_load)
    expect_error(png_with_dimensions(*size), "image/png", 422 if 0 in size else 413,
                 None if 0 in size else DIMENSION_MESSAGE)


@pytest.mark.parametrize("format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
def test_truncated_images_fail_full_validation(format, mime):
    data = photo_bytes(format, size=(128, 96))
    expect_error(data[:len(data) // 2], mime, 422, "damaged or unreadable")


@pytest.mark.parametrize("format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
@pytest.mark.parametrize("missing", [1, 2])
def test_truncated_footer_is_rejected_even_if_pixels_were_complete(format, mime, missing):
    expect_error(photo_bytes(format)[:-missing], mime, 422, "damaged or unreadable")


def test_png_bad_crc_is_not_accepted_as_a_photo():
    data = bytearray(photo_bytes("PNG"))
    data[29] ^= 1
    expect_error(bytes(data), "image/png", 422)


@pytest.mark.parametrize("format,mime", [("PNG", "image/png"), ("WEBP", "image/webp")])
def test_animated_images_are_rejected(format, mime):
    with Image.new("RGB", (32, 16), "red") as first, Image.new("RGB", (32, 16), "blue") as second:
        data = encoded(first, format, save_all=True, append_images=[second], duration=100, loop=0)
    expect_error(data, mime, 422, "still photo")


@pytest.mark.parametrize("orientation", [0, 9, 65535])
def test_invalid_exif_orientation_is_rejected(orientation):
    expect_error(photo_bytes(orientation=orientation), "image/jpeg", 422, "orientation is invalid")


@pytest.mark.parametrize("declared", ["image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"])
def test_heif_declared_types_get_actionable_guidance(declared):
    expect_error(b"not-a-decoded-image", declared, 415, HEIF_MESSAGE)


@pytest.mark.parametrize("declared", [None, "application/octet-stream", "image/jpeg"])
def test_heif_signature_cannot_bypass_policy_with_a_jpeg_name_or_generic_type(declared):
    data = struct.pack(">I", 24) + b"ftyp" + b"heic" + b"\x00" * 4 + b"mif1heic"
    expect_error(data, declared, 415, HEIF_MESSAGE)


def test_empty_random_mismatched_and_unsupported_types():
    expect_error(b"", "image/jpeg", 422, "empty")
    expect_error(b"not-a-photo", "image/jpeg", 422, "damaged")
    expect_error(photo_bytes("PNG"), "image/jpeg", 415, "do not match")
    expect_error(photo_bytes(), "image/svg+xml", 415, "JPEG, PNG, or WebP")
    assert normalize_proof(photo_bytes(), " Image/JPEG; name=ignored ").content_type == "image/jpeg"


def test_raw_bytes_and_saved_bytes_are_independently_bounded():
    expect_error(b"x" * (MAX_RAW_BYTES + 1), "image/jpeg", 413, "size limit")
    expect_error(photo_bytes(), "image/jpeg", 413, "size limit", max_input_bytes=100)
    expect_error(photo_bytes(), "image/jpeg", 413, "saved photo", max_output_bytes=100)


def test_compressed_png_metadata_has_a_separate_decompression_bound():
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Comment", "x" * (64 * 1024 + 1), zip=True)
    with Image.new("RGB", (4, 4)) as source:
        data = encoded(source, "PNG", pnginfo=metadata)
    assert len(data) < 1024
    expect_error(data, "image/png", 422, "damaged or unreadable")


@pytest.fixture
def private_storage(tmp_path):
    storage = LocalObjectStorage(str(tmp_path))
    previous = app.dependency_overrides.get(get_object_storage)
    app.dependency_overrides[get_object_storage] = lambda: storage
    yield storage
    if previous is None:
        app.dependency_overrides.pop(get_object_storage, None)
    else:
        app.dependency_overrides[get_object_storage] = previous


def test_normalized_upload_hashes_saved_bytes_and_duplicate_retry_never_replaces(client, user_factory, private_storage):
    customer, courier, order = create_and_claim_in_transit(client, user_factory)
    raw = photo_bytes(orientation=6)
    first = submit_proof(client, order["id"], courier["headers"], raw)
    assert first.status_code == 201, first.text
    metadata = first.json()
    assert metadata["sha256"] != sha256(raw).hexdigest()
    assert metadata["content_type"] == "image/jpeg" and metadata["storage_key"].endswith(".jpg")
    for retry in (raw, photo_bytes(color=(180, 20, 10))):
        assert submit_proof(client, order["id"], courier["headers"], retry).status_code == 409
        authoritative = client.get(f"/orders/{order['id']}", headers=courier["headers"]).json()["proof"]
        assert authoritative == metadata
    saved = client.get(f"/orders/{order['id']}/proof", headers=customer["headers"])
    assert saved.status_code == 200
    assert len(saved.content) == metadata["size_bytes"]
    assert sha256(saved.content).hexdigest() == metadata["sha256"]
    with Image.open(BytesIO(saved.content)) as image:
        assert image.size == (16, 32) and not image.getexif()
    assert sum(path.is_file() for path in private_storage.root.rglob("*")) == 1


@pytest.mark.parametrize("mime,raw,expected", [
    ("image/jpeg", b"not-a-photo", 422),
    ("image/jpeg", photo_bytes("PNG"), 415),
    ("image/heic", b"synthetic-heic", 415),
    ("image/png", png_with_dimensions(6000, 4000), 413),
])
def test_rejected_upload_creates_no_object_or_proof_and_cannot_complete(client, user_factory, private_storage, mime, raw, expected):
    customer, courier, order = create_and_claim_in_transit(client, user_factory)
    response = client.post(f"/orders/{order['id']}/proof", headers=courier["headers"],
                           files={"file": ("photo", raw, mime)})
    assert response.status_code == expected, response.text
    with SessionLocal() as db:
        assert db.query(DeliveryProof).filter_by(order_id=order["id"]).count() == 0
    assert not any(path.is_file() for path in private_storage.root.rglob("*"))
    assert client.get(f"/orders/{order['id']}/proof", headers=customer["headers"]).status_code == 404
    assert client.patch(f"/orders/{order['id']}/status", headers=courier["headers"], json={"status": "delivered"}).status_code == 409


@pytest.mark.parametrize("mime", [None, "application/octet-stream"])
def test_generic_browser_upload_type_is_decoded_by_endpoint(client, user_factory, private_storage, mime):
    _, courier, order = create_and_claim_in_transit(client, user_factory)
    response = client.post(f"/orders/{order['id']}/proof", headers=courier["headers"],
                           files={"file": ("unknown-format", photo_bytes("PNG"), mime)})
    assert response.status_code == 201, response.text
    assert response.json()["content_type"] == "image/jpeg"


@pytest.mark.parametrize("legacy", [photo_bytes(orientation=6), b"legacy-raw-photo-bytes-before-decoding-was-required"])
def test_existing_proof_bytes_and_recorded_hash_are_never_rewritten(client, user_factory, private_storage, legacy, monkeypatch):
    customer, courier, order = create_and_claim_in_transit(client, user_factory)
    key = f"proofs/{order['id']}/legacy.jpg"
    private_storage.put(key, legacy, "image/jpeg")
    with SessionLocal() as db:
        db.add(DeliveryProof(order_id=order["id"], uploaded_by_user_id=courier["user"]["id"],
                             storage_key=key, content_type="image/jpeg", size_bytes=len(legacy),
                             sha256=sha256(legacy).hexdigest()))
        db.commit()
    def forbidden(*args, **kwargs):
        pytest.fail("Retrieval must not decode or rewrite legacy photos")
    monkeypatch.setattr(proof_routes, "normalize_proof", forbidden)
    retrieved = client.get(f"/orders/{order['id']}/proof", headers=customer["headers"])
    assert retrieved.status_code == 200 and retrieved.content == legacy
    assert retrieved.headers["content-type"] == "image/jpeg"
    assert private_storage.read(key, MAX_RAW_BYTES) == legacy
    assert submit_proof(client, order["id"], courier["headers"]).status_code == 409


def test_busy_decoder_is_retryable_and_releases_after_invalid_photo(client, user_factory, private_storage):
    _, courier, order = create_and_claim_in_transit(client, user_factory)
    assert proof_routes._IMAGE_PROCESSING_SLOT.acquire(blocking=False)
    try:
        busy = submit_proof(client, order["id"], courier["headers"])
        assert busy.status_code == 503 and busy.headers["retry-after"] == "2"
    finally:
        proof_routes._IMAGE_PROCESSING_SLOT.release()
    assert submit_proof(client, order["id"], courier["headers"], b"bad").status_code == 422
    assert submit_proof(client, order["id"], courier["headers"]).status_code == 201


@pytest.mark.parametrize("length_header", [None, "1", "999999999"])
def test_total_multipart_limit_covers_missing_understated_and_oversized_length(client, monkeypatch, length_header):
    monkeypatch.setattr(settings, "CL_PROOF_MAX_BYTES", 1024)
    body = (b"--synthetic-boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"proof.jpg\"\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + b"x" * 70_000 + b"\r\n--synthetic-boundary--\r\n")
    request = client.build_request("POST", "/orders/999/proof", content=body,
                                   headers={"Content-Type": "multipart/form-data; boundary=synthetic-boundary"})
    if length_header is None:
        request.headers.pop("content-length", None)
    else:
        request.headers["content-length"] = length_header
    response = client.send(request)
    assert response.status_code == 413, response.text


def test_malformed_content_length_is_rejected(client):
    request = client.build_request("POST", "/orders/999/proof", content=b"", headers={"Content-Length": "invalid"})
    assert client.send(request).status_code == 400
