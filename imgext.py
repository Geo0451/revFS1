#!/usr/bin/env python3
from PIL import Image

# Fastrack Revolt FS1 resolution is typically 240x286 or 320x386
WIDTH = 240
HEIGHT = 286

with open("final_watchface.bin", "rb") as f:
    data = f.read()

# Search for potential raw RGB565 buffer offsets
# (RGB565 frame size for 240x286 is 240 * 286 * 2 = 137,280 bytes)
frame_size = WIDTH * HEIGHT * 2

for offset in range(0, len(data) - frame_size, 1024):
    raw_rgb = data[offset : offset + frame_size]
    
    img = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = []
    
    for i in range(0, len(raw_rgb), 2):
        pixel = int.from_bytes(raw_rgb[i:i+2], byteorder="little")
        # Extract RGB565 bits
        r = ((pixel >> 11) & 0x1F) << 3
        g = ((pixel >> 5) & 0x3F) << 2
        b = (pixel & 0x1F) << 3
        pixels.append((r, g, b))
        
    img.putdata(pixels)
    img.save(f"frame_offset_{offset}.png")

print("Extraction complete. Check extracted PNG files.")
