#!/usr/bin/env python3
"""
Minimal RAR reader: lists entries and extracts the ones that are stored
uncompressed or use a method this script can handle.

No `unrar` binary is available in this environment and the format is not in the
standard library, so the archive headers are parsed directly. Both RAR4
(`Rar!\\x1a\\x07\\x00`) and RAR5 (`Rar!\\x1a\\x07\\x01\\x00`) are supported for
listing; extraction only covers the "store" method, which is what most game
assets in a RAR actually use for already-compressed data such as PNG and JPEG.

Usage:
    python3 tools/rar_list.py <archive.rar> [outdir]
"""

import os
import struct
import sys
import zlib


def _vint(data, pos):
    """RAR5 variable length integer."""
    value, shift = 0, 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if not b & 0x80:
            break
        shift += 7
    return value, pos


def list_rar5(data):
    entries = []
    pos = 8
    while pos < len(data) - 8:
        if pos + 4 > len(data):
            break
        pos += 4                                     # header CRC
        size, pos = _vint(data, pos)
        header_start = pos
        htype, pos = _vint(data, pos)
        flags, pos = _vint(data, pos)

        extra = 0
        datasize = 0
        if flags & 0x0001:
            extra, pos = _vint(data, pos)
        if flags & 0x0002:
            datasize, pos = _vint(data, pos)

        if htype == 2:                               # file header
            fflags, pos = _vint(data, pos)
            unpacked, pos = _vint(data, pos)
            attr, pos = _vint(data, pos)
            if fflags & 0x0002:
                pos += 4                             # mtime
            if fflags & 0x0004:
                pos += 4                             # crc
            comp, pos = _vint(data, pos)
            host, pos = _vint(data, pos)
            namelen, pos = _vint(data, pos)
            name = data[pos:pos + namelen].decode("utf8", "replace")
            method = (comp >> 7) & 0x07
            entries.append({
                "name": name,
                "size": unpacked,
                "packed": datasize,
                "method": method,
                "offset": header_start + size,
                "dir": bool(attr & 0x10) or (fflags & 0x0001) != 0,
            })

        pos = header_start + size + datasize
    return entries


def list_rar4(data):
    entries = []
    pos = data.find(b"Rar!\x1a\x07\x00") + 7
    while pos + 11 <= len(data):
        crc, htype, flags, hsize = struct.unpack_from("<HBHH", data, pos)
        if hsize < 7:
            break
        addsize = 0
        if flags & 0x8000 and pos + 11 <= len(data):
            addsize = struct.unpack_from("<I", data, pos + 7)[0]

        if htype == 0x74:                            # file header
            (packed, unpacked, host, filecrc, ftime, ver, method,
             namelen, attr) = struct.unpack_from("<IIBIIBBHI", data, pos + 7)
            name = data[pos + 32:pos + 32 + namelen]
            name = name.split(b"\x00")[0].decode("utf8", "replace")
            entries.append({
                "name": name.replace("\\", "/"),
                "size": unpacked,
                "packed": packed,
                "method": method - 0x30,             # 0x30 = store
                "offset": pos + hsize,
                "dir": bool(attr & 0x10),
            })
            pos += hsize + packed
            continue

        pos += hsize + addsize
        if hsize + addsize == 0:
            break
    return entries


def read_rar(path):
    data = open(path, "rb").read()
    if data[:8] == b"Rar!\x1a\x07\x01\x00":
        return data, list_rar5(data), 5
    if data[:7] == b"Rar!\x1a\x07\x00":
        return data, list_rar4(data), 4
    raise ValueError("not a RAR archive: %s" % path)


def extract(path, outdir):
    data, entries, ver = read_rar(path)
    os.makedirs(outdir, exist_ok=True)
    done, skipped = 0, []
    for e in entries:
        if e["dir"] or e["size"] == 0:
            continue
        raw = data[e["offset"]:e["offset"] + e["packed"]]
        if e["method"] != 0:
            skipped.append((e["name"], e["method"]))
            continue
        dest = os.path.join(outdir, e["name"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        open(dest, "wb").write(raw)
        done += 1
    return entries, done, skipped, ver


def main():
    path = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else None
    data, entries, ver = read_rar(path)
    print("%s  (RAR%d, %d entries)" % (os.path.basename(path), ver, len(entries)))
    for e in entries:
        kind = "dir " if e["dir"] else "file"
        print("  %s %-58s %9d bytes  method %d"
              % (kind, e["name"][:58], e["size"], e["method"]))
    if outdir:
        _e, done, skipped, _v = extract(path, outdir)
        print("extracted %d files to %s" % (done, outdir))
        if skipped:
            print("skipped %d compressed entries:" % len(skipped))
            for name, m in skipped[:10]:
                print("   %s (method %d)" % (name, m))


if __name__ == "__main__":
    main()
