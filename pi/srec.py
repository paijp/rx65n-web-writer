"""Motorola S-record and raw binary loading, collapsed into contiguous chunks."""


class LoadError(Exception):
    pass


def parse_srec(text):
    """Return [(address, bytes)] from S-record text, merging adjacent records."""
    chunks = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line[0] != "S":
            continue
        kind = line[1]
        addr_len = {"1": 4, "2": 6, "3": 8}.get(kind)
        if addr_len is None:
            continue  # header, count and termination records carry no data
        try:
            count = int(line[2:4], 16)
            body = bytes.fromhex(line[4:4 + count * 2])
        except ValueError:
            raise LoadError("line %d is not valid hex: %s" % (lineno, line[:20]))
        if len(body) != count:
            raise LoadError("line %d: byte count %d does not match data" % (lineno, count))
        if (sum(body) + count) & 0xFF != 0xFF:
            raise LoadError("line %d: checksum mismatch" % lineno)
        nbytes = addr_len // 2
        addr = int.from_bytes(body[:nbytes], "big")
        data = body[nbytes:-1]
        if chunks and chunks[-1][0] + len(chunks[-1][1]) == addr:
            chunks[-1][1].extend(data)
        else:
            chunks.append((addr, bytearray(data)))
    return [(a, bytes(d)) for a, d in chunks]


def load(path, base=None):
    """Load a .mot/.srec/.s19 file, or a raw binary when `base` is given."""
    raw = open(path, "rb").read()
    if base is not None:
        return [(base, raw)]
    if raw[:1] != b"S":
        raise LoadError("%s does not look like an S-record; pass a load address "
                        "to treat it as a raw binary" % path)
    return parse_srec(raw.decode("ascii", "replace"))


def summarise(chunks):
    return "\n".join("  %08X-%08X  %d bytes" % (a, a + len(d) - 1, len(d))
                     for a, d in chunks)
