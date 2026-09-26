#!/usr/bin/env python3

input_file = "upload.bin"
output_file = "raw_stream.bin"

with open(input_file, "rb") as f:
    data = f.read()

# Split by the magic packet header ffff4247 (\xff\xffBG)
chunks = data.split(b"\xff\xffBG")

clean_payload = bytearray()

for chunk in chunks:
    if not chunk:
        continue
    # Drop initial handshake strings if present
    if b"Geo K J" in chunk:
        continue
    # Strip the 12-byte GATT header remaining at the front of each chunk
    if len(chunk) > 12:
        clean_payload.extend(chunk[12:])

with open(output_file, "wb") as f:
    f.write(clean_payload)

print(f"Extracted {len(clean_payload)} bytes to {output_file}")
