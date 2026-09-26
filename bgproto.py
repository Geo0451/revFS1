#!/usr/bin/env python3
"""
bgproto.py - framing for the Fastrack Revoltt FS1 "FF FF 42 47" (BG) BLE protocol,
as decoded from one HCI capture of a store watch-face upload.

Everything here is REVERSE-ENGINEERED from a single capture. `verify` proves the
builder reproduces that capture byte for byte; it does not prove the watch accepts
anything you build. Facts vs. guesses are listed in README.md.

Usage:
  python3 bgproto.py reassemble out.txt -o clean.bin     # captured writes -> face file
  python3 bgproto.py verify     out.txt                  # rebuild the writes, compare to capture
  python3 bgproto.py build      face.bin -o writes.txt   # face file -> write sequence
  python3 bgproto.py info       face.bin                 # show resource table / image location

Capture format (same as your out.txt): one write per line, "<frame_no>\t<hex bytes>".
"""
import argparse, struct, sys

MAGIC    = b"\xff\xffBG"
CMD_DATA = 0x03
TYPE_FACE = 0xF4     # type byte seen on the face upload (what it means is unconfirmed)
BLOCK    = 2048      # file bytes per block
PAYLOAD  = 242       # payload bytes per BLE write (244-byte write minus 2-byte prefix)

# --- layout of the ONE store face we captured (may differ on other faces) ---
IMG_W, IMG_H = 240, 284
RES_BASE     = 0x9FC   # resource-table offsets are relative to this
RES_TABLE    = 0xA44   # 7 little-endian u32 offsets
RES_COUNT    = 7


def checksum(data: bytes) -> int:
    """One's complement of the byte sum, carries folded back in (16-bit)."""
    s = sum(data)
    while s >> 16:
        s = (s & 0xFFFF) + (s >> 16)
    return ~s & 0xFFFF


def header(cmd: int, typ: int, length: int, params: bytes) -> bytes:
    """20-byte header: magic | 01 00 | cmd | type | len(u16 BE) | 8 param bytes | checksum."""
    assert len(params) == 8
    body = MAGIC + b"\x01\x00" + bytes([cmd, typ]) + struct.pack(">H", length) + params
    return body + struct.pack(">H", checksum(body))


def data_frames(payload: bytes, chunk: int = PAYLOAD):
    """Split a payload into BLE writes: [frame idx][running byte count mod 256][<=chunk bytes]."""
    out, total = [], 0
    for i in range(0, len(payload), chunk):
        part = payload[i:i + chunk]
        total += len(part)
        out.append(bytes([len(out) & 0xFF, total & 0xFF]) + part)
    return out


def build_transfer(data: bytes, typ: int = TYPE_FACE, block: int = BLOCK, chunk: int = PAYLOAD):
    """Return the ordered list of BLE writes for uploading `data` as one file."""
    nblocks = -(-len(data) // block)
    writes = []
    for k in range(nblocks):
        part = data[k * block:(k + 1) * block]
        payload = part + struct.pack(">H", checksum(part))
        params = struct.pack(">IHH", k * block, nblocks, k + 1)   # offset, total blocks, block no.
        writes.append(header(CMD_DATA, typ, len(payload), params))
        writes += data_frames(payload, chunk)
    return writes


# ---------------------------------------------------------------- capture parsing
def read_capture(path):
    rows = []
    for line in open(path):
        p = line.rstrip("\n").split("\t")
        if len(p) == 2 and p[1]:
            rows.append((int(p[0]), bytes.fromhex(p[1])))
    return rows


def group_messages(rows):
    """[(header, [data writes])] - a header write starts each message."""
    msgs = []
    for _, b in rows:
        if len(b) == 20 and b[:4] == MAGIC:
            msgs.append((b, []))
        elif msgs:
            msgs[-1][1].append(b)
    return msgs


def find_uploads(rows, typ=TYPE_FACE):
    """Return a list of uploads; each is a list of (header, frames) block messages."""
    uploads = []
    for hdr, frames in group_messages(rows):
        if hdr[6] != CMD_DATA or hdr[7] != typ:
            continue
        if checksum(hdr[:-2]) != struct.unpack(">H", hdr[-2:])[0]:
            raise ValueError("header checksum mismatch")
        index = struct.unpack(">H", hdr[16:18])[0]
        if index == 1 or not uploads:
            uploads.append([])
        uploads[-1].append((hdr, frames))
    return uploads


def reassemble(blocks):
    """Blocks of one upload -> file bytes. Verifies lengths, offsets, order and checksums."""
    out = bytearray()
    total_expected = None
    for n, (hdr, frames) in enumerate(blocks, 1):
        length = struct.unpack(">H", hdr[8:10])[0]
        offset, total, index = struct.unpack(">IHH", hdr[10:18])
        if index != n or offset != len(out):
            raise ValueError(f"block {n}: unexpected index/offset ({index}, {offset})")
        body = b"".join(f[2:] for f in frames)
        if len(body) != length:
            raise ValueError(f"block {n}: length {len(body)} != header {length}")
        data, ck = body[:-2], struct.unpack(">H", body[-2:])[0]
        if checksum(data) != ck:
            raise ValueError(f"block {n}: data checksum mismatch")
        out += data
        total_expected = total
    if total_expected != len(blocks):
        raise ValueError(f"capture has {len(blocks)} blocks but header says {total_expected}")
    return bytes(out)


# ---------------------------------------------------------------- face layout helpers
def resource_table(face: bytes):
    return [struct.unpack_from("<I", face, RES_TABLE + 4 * i)[0] + RES_BASE for i in range(RES_COUNT)]


def image_offset(face: bytes) -> int:
    off = resource_table(face)[0]
    nxt = resource_table(face)[1]
    if nxt - off != IMG_W * IMG_H * 2:
        raise ValueError("asset 0 is not exactly 240x284x2 bytes - this face has a different layout")
    return off


# ---------------------------------------------------------------- CLI
def cmd_reassemble(a):
    ups = find_uploads(read_capture(a.capture))
    if not ups:
        sys.exit("no face upload found in capture")
    print(f"found {len(ups)} upload(s); using #{a.which}")
    data = reassemble(ups[a.which])
    open(a.o, "wb").write(data)
    print(f"wrote {a.o}: {len(data)} bytes, all checksums OK")


def cmd_verify(a):
    rows = read_capture(a.capture)
    ups = find_uploads(rows)
    if not ups:
        sys.exit("no face upload found in capture")
    blocks = ups[a.which]
    data = reassemble(blocks)
    captured = [w for hdr, frames in blocks for w in [hdr] + frames]
    rebuilt = build_transfer(data)
    if captured == rebuilt:
        print(f"PASS: rebuilt {len(rebuilt)} writes from a {len(data)}-byte file; identical to the capture")
        return
    print(f"FAIL: captured {len(captured)} writes, rebuilt {len(rebuilt)}")
    for i, (x, y) in enumerate(zip(captured, rebuilt)):
        if x != y:
            print(f"first difference at write #{i}:\n captured {x.hex()}\n rebuilt  {y.hex()}")
            break
    sys.exit(1)


def cmd_build(a):
    face = open(a.face, "rb").read()
    writes = build_transfer(face)
    with open(a.o, "w") as f:
        for i, w in enumerate(writes, 1):
            f.write(f"{i}\t{w.hex()}\n")
    print(f"wrote {a.o}: {len(writes)} writes for a {len(face)}-byte file")


def cmd_info(a):
    face = open(a.face, "rb").read()
    print(f"file size {len(face)} bytes ({len(face)/BLOCK:.2f} blocks)")
    tbl = resource_table(face)
    for i, off in enumerate(tbl):
        end = tbl[i + 1] if i + 1 < len(tbl) else len(face)
        print(f" asset {i}: offset 0x{off:X}  size {end - off}")
    try:
        print(f"main image: 0x{image_offset(face):X}, 240x284 RGB565 big-endian")
    except ValueError as e:
        print("main image: NOT CONFIRMED -", e)


def cmd_context(a):
    """Show every message in a capture, and highlight what brackets the file-transfer run —
    use this to find the start/commit commands that might be missing from a replay."""
    rows = read_capture(a.capture)
    msgs = group_messages(rows)
    f4 = [i for i, (h, _) in enumerate(msgs) if h[6] == CMD_DATA and h[7] == a.type]
    if not f4:
        sys.exit(f"no messages with cmd=3 type=0x{a.type:02X} found in this capture")
    print(f"{len(msgs)} total messages; file-transfer run is idx {f4[0]}..{f4[-1]} ({len(f4)} messages)")
    print()

    def show(i):
        h, frames = msgs[i]
        cmd, typ = h[6], h[7]
        length = struct.unpack(">H", h[8:10])[0]
        params = h[10:18]
        data = b"".join(f[2:] for f in frames)
        tag = "F4-RUN" if i in f4 else ("BEFORE" if i < f4[0] else "AFTER")
        print(f"[{tag}] idx{i}: cmd=0x{cmd:02X} type=0x{typ:02X} len={length} "
              f"params={params.hex()} frames={len(frames)}"
              + (f" data={data[:40].hex()}{'...' if len(data) > 40 else ''}" if data and typ != a.type else ""))

    n = a.context
    print(f"--- {n} messages before the run ---")
    for i in range(max(0, f4[0] - n), f4[0]):
        show(i)
    print(f"--- run start/end (collapsed) ---")
    show(f4[0])
    if len(f4) > 1:
        print(f"  ... {len(f4) - 2} more type=0x{a.type:02X} messages ...")
        show(f4[-1])
    print(f"--- {n} messages after the run ---")
    for i in range(f4[-1] + 1, min(len(msgs), f4[-1] + 1 + n)):
        show(i)
    if f4[-1] + 1 >= len(msgs):
        print("  (nothing — the capture ends right at the last file-transfer block.")
        print("   if the watch didn't visibly update, you likely need a wider capture")
        print("   window: start recording before opening the app, stop well after the")
        print("   watch finishes updating.)")


def cmd_extract(a):
    """Pull raw header+frame writes from N messages before/after the file-transfer run,
    written in the same tab-separated format as a replay writes file, so they can be
    prepended/appended directly to a writes.txt used for replay."""
    rows = read_capture(a.capture)
    msgs = group_messages(rows)
    f4 = [i for i, (h, _) in enumerate(msgs) if h[6] == CMD_DATA and h[7] == a.type]
    if not f4:
        sys.exit(f"no messages with cmd=3 type=0x{a.type:02X} found in this capture")

    before = msgs[max(0, f4[0] - a.before):f4[0]]
    after = msgs[f4[-1] + 1: f4[-1] + 1 + a.after]
    if not before and not after:
        sys.exit("nothing to extract — pass --before and/or --after")

    out = []
    for h, frames in before + after:
        out.append(h)
        out.extend(frames)
    with open(a.o, "w") as f:
        for i, w in enumerate(out, 1):
            f.write(f"{i}\t{w.hex()}\n")
    print(f"wrote {a.o}: {len(before)} message(s) before + {len(after)} message(s) after "
          f"= {len(out)} raw writes")
    if before:
        print("NOTE: these came from a real capture, not from bgproto's own builder — send them")
        print("      exactly as-is (same bytes, same order) before your file-transfer writes.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("reassemble"); x.add_argument("capture"); x.add_argument("-o", required=True)
    x.add_argument("--which", type=int, default=-1, help="which upload in the capture (default: last)")
    x.set_defaults(fn=cmd_reassemble)
    x = s.add_parser("verify"); x.add_argument("capture")
    x.add_argument("--which", type=int, default=-1); x.set_defaults(fn=cmd_verify)
    x = s.add_parser("build"); x.add_argument("face"); x.add_argument("-o", required=True); x.set_defaults(fn=cmd_build)
    x = s.add_parser("info"); x.add_argument("face"); x.set_defaults(fn=cmd_info)
    x = s.add_parser("context", help="show what brackets the file-transfer run in a capture")
    x.add_argument("capture")
    x.add_argument("--type", type=lambda v: int(v, 0), default=TYPE_FACE, help="message type to bracket (default 0xF4)")
    x.add_argument("--context", type=int, default=10, help="how many messages to show on each side")
    x.set_defaults(fn=cmd_context)
    x = s.add_parser("extract", help="pull out N messages before/after the file-transfer run as a writes.txt-style file")
    x.add_argument("capture")
    x.add_argument("-o", required=True)
    x.add_argument("--type", type=lambda v: int(v, 0), default=TYPE_FACE)
    x.add_argument("--before", type=int, default=0, help="how many messages before the run to include")
    x.add_argument("--after", type=int, default=0, help="how many messages after the run to include")
    x.set_defaults(fn=cmd_extract)
    a = p.parse_args(); a.fn(a)
