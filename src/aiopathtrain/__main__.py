import asyncio
from contextlib import suppress
from datetime import datetime

from aiopathtrain import Direction, PATHRealtimeClient, fetch_token_metadata


async def main():
    token_metadata = await fetch_token_metadata()
    client = PATHRealtimeClient(token_metadata)

    station = "Exchange Place"
    direction: Direction = "New York"

    print(f"[PATH] 📍 {station} → {direction} | listening... (Ctrl+C to stop)\n")
    with suppress(asyncio.CancelledError):
        async for arrival in client.listen(station, direction):
            if arrival.seconds_to_arrival == 0:
                time_str = "NOW"
            else:
                mins = arrival.seconds_to_arrival // 60
                secs = arrival.seconds_to_arrival % 60
                time_str = f"{mins}m {secs}s"
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            print(f"{now}  🚊 A PATH train to {arrival.headsign} arrives in {time_str}")
            print()


if __name__ == "__main__":
    asyncio.run(main())
