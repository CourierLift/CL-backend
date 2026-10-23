"""Small genuine synthetic images for upload tests; no customer photos."""

from functools import lru_cache
from io import BytesIO

from PIL import Image


@lru_cache(maxsize=32)
def photo_bytes(format="JPEG", *, size=(32, 16), color=(31, 110, 180), orientation=None):
    with Image.new("RGB", size, color) as image, BytesIO() as output:
        options = {}
        if orientation is not None:
            exif = Image.Exif()
            exif[274] = orientation
            options["exif"] = exif
        if format == "JPEG":
            options.update(quality=95, subsampling=0)
        image.save(output, format=format, **options)
        return output.getvalue()
