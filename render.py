#!/usr/bin/env python3
from PIL import Image

# Common watch asset dimensions to test
WIDTH = 240
HEIGHT = 284
OFFSET = 592  # Payload start found earlier

with open("final_watchface.bin", "rb") as f:
    f.seek(OFFSET)
    raw_bytes = f.read()

# 1. Decode as Little-Endian RGB565
rgb565_img = Image.new("RGB", (WIDTH, HEIGHT))
pixels_565 = []
total_pixels = WIDTH * HEIGHT

for i in range(0, min(len(raw_bytes), total_pixels * 2), 2):
    pixel = int.from_bytes(raw_bytes[i:i+2], byteorder="little")
    r = ((pixel >> 11) & 0x1F) << 3
    g = ((pixel >> 5) & 0x3F) << 2
    b = (pixel & 0x1F) << 3
    pixels_565.append((r, g, b))

if len(pixels_565) == total_pixels:
    rgb565_img.putdata(pixels_565)
    rgb565_img.save("extracted_rgb565.png")
    print("Saved extracted_rgb565.png")

# 2. Decode as 8-bit Alpha/Grayscale (Used for digit fonts)
gray_img = Image.new("L", (WIDTH, HEIGHT))
if len(raw_bytes) >= total_pixels:
    gray_img.putdata(list(raw_bytes[:total_pixels]))
    gray_img.save("extracted_grayscale.png")
    print("Saved extracted_grayscale.png")
