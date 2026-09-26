#!/usr/bin/env python3

with open("final_watchface.bin", "rb") as f:
    f.seek(185404)
    asset_data = f.read()

print(f"Asset block length: {len(asset_data)} bytes")
print("First 64 bytes of asset payload (hex):")
print(asset_data[:64].hex(' '))

# Check for standard image signatures
if asset_data.startswith(b'\x89PNG'):
    print("\nResult: Asset is a PNG image!")
    with open("extracted_icon.png", "wb") as out:
        out.write(asset_data)
elif asset_data.startswith(b'BM'):
    print("\nResult: Asset is a BMP image!")
    with open("extracted_icon.bmp", "wb") as out:
        out.write(asset_data)
else:
    print("\nResult: Custom/RLE indexed font or glyph sprite sheet.")
