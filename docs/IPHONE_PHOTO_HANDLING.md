# iPhone delivery-proof photos: prepared contract and evidence

Prepared 22 October 2026. These changes are local release preparation. No live
photo, credential, bucket, service setting, or deployment was changed. The
published theme, NavGlass, and disabled-payment behavior are unchanged.

## What the candidate accepts and saves

The browser uploads the exact selected `File`; it does not use canvas, resize,
re-encode, or replace it during a retry. The server identifies and fully decodes
the actual bytes before allowing a new delivery proof into storage.

| Boundary | Prepared behavior |
| --- | --- |
| Browser selection | JPEG, PNG, or WebP, at most **4,000,000 bytes (decimal 4 MB)**. Empty MIME or `application/octet-stream` can reach the server for genuine byte identification. |
| Direct API upload | At most **10,485,760 bytes (10 MiB)**, or a smaller configured `CL_PROOF_MAX_BYTES`. This is not a larger browser allowance. |
| Multipart request | Bounded before form parsing/spooling to the API file limit plus 65,536 bytes of framing. Actual received bytes are counted even with a missing or understated Content-Length. |
| Genuine image types | Static JPEG, PNG, and WebP only. A conflicting declared MIME is rejected even if another supported decoder can read the bytes. Filenames are not evidence of format. |
| Dimensions before pixel load | At most **16,000,000 pixels** and **8192 pixels on either axis**. A 4032×3024 image is accepted; 24- or 48-megapixel files must be exported smaller. |
| Orientation | All eight EXIF orientations are applied to pixels before saving. No orientation flag remains necessary to display the result correctly. |
| Saved image | RGB JPEG, quality 85, longest side at most 2048 pixels, at most 4,000,000 bytes and no larger than the configured API limit. Transparency is composited on white. |
| Metadata | A fresh RGB image is created from decoded pixels; EXIF/GPS, XMP, ICC profiles, comments, PNG text, and appended non-image payloads are not copied. |
| Proof record | Existing schema and response shape. `content_type`, `size_bytes`, and `sha256` describe the **saved JPEG bytes**, with a new random `.jpg` object key. |
| Earlier proofs | Existing rows and stored objects are preserved exactly. GET does not decode, rotate, scrub, resize, or re-encode them. Their recorded hashes continue to describe their original stored bytes. |

New proofs therefore lose metadata and are a lossy visual rendition of the
selected photo. They do not preserve its original encoding, camera settings,
capture timestamp, or raw-file hash. ICC profiles are stripped without a color
management conversion; wide-gamut/HDR appearance can change. The pilot should
use ordinary still photos that remain legible after conversion. Neither this
normalization nor a file timestamp establishes when or where a delivery occurred.

The implementation is `backend/services/proof_images.py`, called by
`backend/routes/proofs.py`. It changes no database migration and introduces no
HEIF plugin, external image service, new worker service, or paid resource.

## HEIC/HEIF and physical iPhone behavior

HEIC/HEIF is deliberately unsupported. Explicit HEIC MIME types and recognizable
HEIF container brands receive an actionable 415 error; they are never handed to
a new decoder. Export an existing photo to JPEG, PNG, or WebP and choose that
copy. For future captures, Apple's documented setting is **Settings → Camera →
Formats → Most Compatible**, which uses JPEG for new photos. That setting does
not convert an existing HEIC photo. Apple documents manual export and notes that
automatic compatibility conversion depends on the sharing/import path; this
release does not assume Safari always converts the selected photo. See
[Apple's HEIF/HEVC guidance](https://support.apple.com/116944).

The browser keeps library and Files selection available; it does not force a
camera-only capture flow. Its selection help exposes the type, byte, and pixel
limits, and its error text explains the existing-file export versus future-camera
setting distinction. A valid JPEG returned by an iPhone picker is accepted even
if its name retains an unrelated extension; the bytes and declared type decide.

Animated WebP/APNG, videos, PDF, SVG, GIF, TIFF, AVIF, RAW/ProRAW, and a whole Live
Photo are not supported uploads. A supported still export is required. A 12MP
image still has to meet the byte cap; passing the pixel limit alone is not enough.

## Error and retry contract

All route errors use the existing JSON `detail` field. No proof row or stored
object is created by a failed image validation, and the order cannot be marked
delivered without a saved proof.

| Status | Condition and user-facing message |
| --- | --- |
| 201 | One new proof was normalized and saved. The returned hash is the saved JPEG's hash. |
| 409 | `Proof has already been submitted`; the existing one-proof-per-order record is kept. The existing in-transit and assigned-courier guards also remain. |
| 413 | Raw file exceeds the configured/hard API limit: `Proof file exceeds the configured size limit.` The multipart wrapper has equivalent upload-size wording. |
| 413 | Pixel or axis limit: `This photo is too large to process. Choose a photo up to 16 megapixels and 8192 pixels per side, or export a smaller copy.` |
| 413 | Bounded JPEG output would be too large: `The saved photo would be too large. Choose a smaller photo.` |
| 415 | HEIC/HEIF: `HEIC/HEIF photos are not supported. Choose a JPEG, PNG, or WebP export. For new iPhone photos, use Camera > Formats > Most Compatible.` |
| 415 | Explicit unsupported MIME: `Proof must be a JPEG, PNG, or WebP photo.` |
| 415 | Declared/decoded mismatch: `The photo contents do not match its file type. Export a new JPEG, PNG, or WebP copy.` |
| 422 | Empty: `Proof file is empty.` Damaged/unrecognized/truncated bytes: `This photo is damaged or unreadable. Choose a new JPEG, PNG, or WebP photo.` |
| 422 | Animation/multiple frames: `Animated or multiple-frame photos are not supported. Choose one still photo.` |
| 422 | Invalid orientation: `The photo orientation is invalid. Export a new JPEG, PNG, or WebP copy.` |
| 503 | Another normalization is active: `Photo processing is busy. Try again in a moment.` with `Retry-After: 2`. Storage failure remains `Unable to store delivery proof`. |

A timeout, disconnected response, or 409 can mean the first upload succeeded.
The frontend reloads the authoritative order. If a proof exists, it asks the
courier to view that saved proof; it does **not** claim the current selection was
the same file. If the server confirms no proof exists, a deliberate retry sends
the same original `File` bytes. No new raw-upload hash field or request-id token
was introduced. The database uniqueness constraint and route guard prevent
replacement; a simultaneous loser cleans up only its own new random key.

The existing authenticated GET continues to require the order's customer,
assigned courier, or admin, and checks stored size and SHA256 before returning
the image with private/no-store and nosniff headers. A corrupt or missing stored
object returns 503. Normalization is never applied during retrieval, migration,
backup, or recovery, so preserved proof hashes remain meaningful.

## Decoder and resource limits

The dependency is pinned to **Pillow 13.0.0**, installed and tested with Python
3.11.17. The local wheel reports JPEG, WebP, and zlib/PNG support. Deployment must
install that pin and verify the same codecs; the local installation is not proof
of a hosted build. References: [Pillow 13.0.0 package](https://pypi.org/project/pillow/13.0.0/)
and [supported Python versions](https://pillow.readthedocs.io/en/stable/installation/python-support.html).

`Image.open` is restricted to JPEG/PNG/WebP, then verification and full strict
pixel loading are performed. Truncated-image loading stays disabled. Both
Pillow decompression warnings and errors fail closed; explicit axis/pixel checks
run before pixel decode. PNG compressed text is limited to 64 KiB per chunk and
256 KiB in total. A bounded PNG chunk walk also requires its complete IEND
checksum, because Pillow alone can accept a partially missing terminal CRC.
The output writer stops at its cap. These controls follow the relevant
[Pillow security guidance](https://pillow.readthedocs.io/en/stable/handbook/security.html)
and use its [EXIF transpose operation](https://pillow.readthedocs.io/en/stable/reference/ImageOps.html#PIL.ImageOps.exif_transpose).

Only **one normalization per application process** runs at once, on an AnyIO
thread rather than the event loop. A competing upload receives retryable 503;
there is no in-memory work queue. Keep the reviewed web process at one Uvicorn
worker on the existing 512 MB service; adding workers multiplies decoder memory.
Multipart and decoder limits reduce exposure but are not an OS memory sandbox
or a hard CPU-time kill. Pillow's native parsing remains trusted code; monitor
security releases and test before changing its pin.

Independent local-process measurements, with the full backend imported and
synthetic uniform pixels, are recorded in
`evidence/photo-normalization-metrics-20261022.json`:

| Input | Saved size | Normalization time | Peak process RSS |
| --- | --- | ---: | ---: |
| 4032×3024 JPEG, EXIF6 | 1536×2048, 18,718 bytes | 0.310 s | 172.4 MiB |
| 4000×4000 RGBA PNG, EXIF6 | 2048×2048, 24,862 bytes | 0.754 s | 249.3 MiB |
| 4032×3024 WebP, EXIF6 | 1536×2048, 18,718 bytes | 0.352 s | 268.2 MiB |

These are measured examples, not worst-case bounds or a 512 MB hosted acceptance
result. Real texture, metadata, concurrent requests, native allocator behavior,
and the deployed baseline can change both time and memory. The approved release
must verify representative real iPhone photos under the actual Render limit,
including a rejected concurrent upload and a successful retry, without an OOM
or request timeout. No resource upgrade is authorized by these measurements.

## Isolated evidence and remaining release checks

Fresh local SQLite result: **75 passed**; the same subset also passed **75/75
on PostgreSQL 16** in a dedicated disposable loopback database. The subset is `tests/test_proof_images.py`,
`tests/test_delivery_proof.py`, and `tests/test_unpaid_pilot.py`. This includes
66 new decoder/route cases: genuine JPEG/PNG/WebP, all eight EXIF transforms,
metadata stripping, alpha compositing, 12MP acceptance, 24/48MP and axis bounds,
animation, malformed/truncated streams including partial footers, MIME mismatch,
HEIC, compressed metadata, byte/output bounds, multipart length lies, retryable
busy handling, normalized SHA, duplicate-file preservation, and untouched legacy
raw GET. A further **38 affected delivery workflow regression tests passed**
after replacing their arbitrary-byte success fixtures with genuine images.
The only emitted warning is the existing upstream Starlette/AnyIO
BlockingPortal deprecation.

The frontend's independent local PostgreSQL and Chromium two-role harness passed
10/10 steps, including EXIF6 JPEG display at 16×32, original-byte retry after a
deliberate 503, customer/courier saved histories, authorized saved-byte hash,
unrelated-user 403, and persistence across API restart. This is local evidence;
a Chromium mobile viewport is not physical Safari.

Before hosted acceptance can pass, record all of the following using synthetic
subjects and addresses:

1. Real iPhone model/iOS/Safari version; JPEG selection from Photos and Files,
   a new Most Compatible capture, and a genuine HEIC selection that receives
   the intended guidance. Record what MIME/bytes the picker actually sends.
2. Portrait and landscape photos with orientation flags; saved upright preview
   after a fresh login as both customer and courier. Confirm no private metadata
   is present in the newly stored image, while an earlier proof stays unchanged.
3. Normal 12MP acceptance within 4 MB, over-4-MB browser feedback, 24/48MP server
   feedback, damaged-file feedback, and recovery after mobile network interruption.
4. Actual deployed codec availability and memory/latency at the single-worker
   512 MB service; the complete quote → order → claim → pickup → transit → proof
   → delivery → saved-history path with payments disabled.
5. Private hosted S3 upload/read, restart persistence, and a coordinated encrypted
   database/object checkpoint restored into fresh isolated targets with the same
   saved-object hashes. See [PILOT_PHOTO_STORAGE.md](PILOT_PHOTO_STORAGE.md) and
   [AUTOMATED_BACKUPS.md](AUTOMATED_BACKUPS.md).

These physical-device and hosted gates remain blocked on the combined approved
release and access to the actual test device; they are not reported as passed.
