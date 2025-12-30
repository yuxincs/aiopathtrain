#!/usr/bin/env python3
"""Test the live PATH feed for a short period"""

import asyncio
import sys
from path_train import PathRealtimeClient

async def test_live():
    client = PathRealtimeClient()
    
    print("Testing live PATH data for 30 seconds...")
    
    # Connect to one direction only for testing
    conn = await client.connect_to_station("Grove Street", "New York")
    await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(test_live())
