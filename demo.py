#!/usr/bin/env python3
"""Test the live PATH feed for a short period"""

import asyncio

from path_train import PathRealtimeClient


async def test_live():
    client = PathRealtimeClient()

    print("Listening for train messages...")

    # Connect to one direction only for testing
    async for arrival in client.listen("Grove Street", "New York"):
        # Display the trains
        print(f"📍 {arrival.station} → {arrival.direction}")
        if arrival.seconds_to_arrival == 0:
            time_str = "NOW"
        else:
            mins = arrival.seconds_to_arrival // 60
            secs = arrival.seconds_to_arrival % 60
            time_str = f"{mins}m {secs}s"
        print(f"  🚊 {arrival.headsign}: {time_str}")
        print()  # Add blank line


if __name__ == "__main__":
    asyncio.run(test_live())
