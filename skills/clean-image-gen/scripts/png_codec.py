# -*- coding: utf-8 -*-
"""Pure-stdlib PNG codec with ancillary-chunk preservation.

Reads 8-bit non-interlaced PNG (grayscale / RGB / RGBA) and writes it back,
preserving ancillary chunks (eXIf, tEXt, ...) so regulatory metadata such as
the TC260 AIGC implicit label survives the watermark-removal re-encode.
"""

import struct
import zlib

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def read_png(path):
    """Return (width, height, channels, pixels, ancillary_chunks).

    ancillary_chunks: list of (type_bytes, data_bytes) for every chunk except
    IHDR / IDAT / IEND, in original order.
    """
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != _PNG_SIG:
        raise ValueError("not a PNG file: %s" % path)
    pos = 8
    width = height = bitdepth = colortype = None
    idat = b""
    ancillary = []
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bitdepth, colortype = struct.unpack(">IIBB", chunk[:10])
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype != b"IEND":
            ancillary.append((ctype, chunk))
        pos += 12 + length
    if width is None or not idat:
        raise ValueError("malformed PNG (missing IHDR/IDAT): %s" % path)
    if bitdepth != 8:
        raise ValueError("only 8-bit PNG supported, got bitdepth=%d" % bitdepth)
    channels = _CHANNELS.get(colortype)
    if channels is None:
        raise ValueError("unsupported PNG color type %d" % colortype)
    raw = zlib.decompress(idat)
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        elif ftype != 0:
            raise ValueError("unsupported PNG filter %d" % ftype)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out), ancillary


def write_png(path, width, height, channels, pixels, ancillary=None):
    """Write an 8-bit PNG; re-emit ancillary chunks after IHDR when given."""
    stride = width * channels
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter None
        raw.extend(pixels[y * stride:(y + 1) * stride])

    def chunk(ctype, payload):
        return (struct.pack(">I", len(payload)) + ctype + payload +
                struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))

    colortype = {1: 0, 3: 2, 4: 6}[channels]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, colortype, 0, 0, 0)
    body = chunk(b"IHDR", ihdr)
    if ancillary:
        for ctype, payload in ancillary:
            if ctype in (b"IHDR", b"IDAT", b"IEND"):
                continue
            body += chunk(ctype, payload)
    body += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    body += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(_PNG_SIG)
        f.write(body)
    return path
