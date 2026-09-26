#!/usr/bin/env python3

with open("final_watchface.bin", "rb") as f:
    data = f.read()

print(f"Total File Size: {len(data)} bytes\n")
print("Non-zero data regions in header (first 2KB):")

in_data = False
start = 0

for i in range(min(2048, len(data))):
    if data[i] != 0 and not in_data:
        in_data = True
        start = i
    elif data[i] == 0 and in_data:
        in_data = False
        print(f"  Region 0x{start:04x} - 0x{i-1:04x} ({i - start} bytes): {data[start:i].hex()}")

# Print first occurrence of heavy non-zero data (likely raw image/font assets)
for i in range(512, len(data) - 16, 16):
    if any(data[i:i+16]):
        print(f"\nFirst payload data block starts at offset: 0x{i:06x} ({i})")
        break
