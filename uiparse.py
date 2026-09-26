#!/usr/bin/env python3
import struct

with open("final_watchface.bin", "rb") as f:
    data = f.read()

print(f"File size: {len(data)} bytes\n")

print("--- Searching for potential 32-bit payload offsets ---")
# Scan the header (0x0000 - 0x0600) for uint32 values that fall within file bounds
for i in range(0, 0x0600, 2):
    val_le = struct.unpack("<I", data[i:i+4])[0]
    val_be = struct.unpack(">I", data[i:i+4])[0]
    
    # Check if value looks like a valid offset pointing into the file payload
    if 0x0200 <= val_le < len(data):
        print(f"Header 0x{i:04x}: Little-Endian Offset -> 0x{val_le:06x} ({val_le})")
    if 0x0200 <= val_be < len(data) and val_be != val_le:
        print(f"Header 0x{i:04x}: Big-Endian Offset    -> 0x{val_be:06x} ({val_be})")
