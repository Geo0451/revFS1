#!/usr/bin/env python3
import asyncio
import sys
from bleak import BleakClient

SERVICE_UUID = "c2e6ffb0-e966-1000-8000-bef9c223df6a"
CHARACTERISTIC_WRITE = "c2e6ffb1-e966-1000-8000-bef9c223df6a"

async def replay_writes(address, writes_file):
    print(f"Connecting to {address}...")
    async with BleakClient(address) as client:
        print(f"Connected")
        
        writes = []
        for line in open(writes_file):
            p = line.rstrip('\n').split('\t')
            if len(p) == 2 and p[1]:
                writes.append(bytes.fromhex(p[1]))
        
        print(f"Replaying {len(writes)} writes...")
        for i, data in enumerate(writes):
            try:
                await client.write_gatt_char(CHARACTERISTIC_WRITE, data, response=True)
                if (i + 1) % 100 == 0:
                    print(f"  {i+1}/{len(writes)}")
            except Exception as e:
                print(f"Write #{i+1} failed: {e}")
                return False
            await asyncio.sleep(0.01)
        
        print("✓ All writes sent successfully")
        return True

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <MAC> <writes.txt>")
        sys.exit(1)
    success = asyncio.run(replay_writes(sys.argv[1], sys.argv[2]))
    sys.exit(0 if success else 1)
