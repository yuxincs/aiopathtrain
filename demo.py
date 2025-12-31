import asyncio

from path_train import PATHRealtimeClient


async def main():
    client = PATHRealtimeClient()

    station, direction = "Exchange Place", "New York"

    print(f"Listening for PATH train arrival messages on {station} (to {direction})...")
    async for arrival in client.listen(station, direction):
        print(f"📍 {arrival.station} → {arrival.direction}")
        if arrival.seconds_to_arrival == 0:
            time_str = "NOW"
        else:
            mins = arrival.seconds_to_arrival // 60
            secs = arrival.seconds_to_arrival % 60
            time_str = f"{mins}m {secs}s"
        print(f"  🚊 {arrival.headsign}: {time_str}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
