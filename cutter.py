#!/usr/bin/env python3

input_file = "upload.bin"
output_file = "final_watchface.bin"

with open(input_file, "rb") as f:
    data = f.read()

chunks = data.split(b"\xff\xffBG")

clean_payload = bytearray()

for c in chunks:
    if not c or b"Geo K J" in c:
        continue
    
    # Fastrack Data Frame structure inside chunk:
    # [12 bytes GATT header] [4 bytes Block Counter] [Payload Data...] [2 bytes CRC]
    if len(c) > 18:
        # Slice past GATT header (12b) and block counter (4b), omitting last 2 CRC bytes
        block_data = c[16:-2]
        clean_payload.extend(block_data)

with open(output_file, "wb") as f:
    f.write(clean_payload)

print(f"Clean Watchface Size: {len(clean_payload)} bytes")
