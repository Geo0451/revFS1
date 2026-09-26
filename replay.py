#!/usr/bin/env python3
import asyncio
import sys
from bleak import BleakClient, BleakScanner

# From bgproto.py or CMDHelper source
SERVICE_UUID = "c2e6ffb0-e966-1000-8000-bef9c223df6a"
CHARACTERISTIC_WRITE = "c2e6ffb1-e966-1000-8000-bef9c223df6a"
CHARACTERISTIC_NOTIFY = "c2e6ffb2-e966-1000-8000-bef9c223df6a"

async def replay_writes(address, writes_file):
    async with BleakScanner() as scanner:
        devices = await scanner.discover()
        device = None
        for d in devices:
            if d.address.lower() == address.lower():
                device = d
                break
        if not device:
            print(f"Device {address} not found")
            return False
    
    async with BleakClient(device.address) as client:
        print(f"Connected to {device.name} ({device.address})")
        
        # Read writes from the file
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
            await asyncio.sleep(0.01)  # 10ms between writes, adjust if needed
        
        print("All writes sent successfully")
        return True

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <watch_mac_address> <writes.txt>")
        print(f"Example: {sys.argv[0]} AA:BB:CC:DD:EE:FF writes.txt")
        sys.exit(1)
    
    address = sys.argv[1]
    writes_file = sys.argv[2]
    
    success = asyncio.run(replay_writes(address, writes_file))
    sys.exit(0 if success else 1)
