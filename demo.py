import asyncio

from path_train import PATHRealtimeClient, fetch_token_metadata


async def main():
    client = PATHRealtimeClient()
    # Or fetch and persist the token metadata for faster initialization (if the metadata is still valid):
    #
    # existing = load_token_metadata_from_storage() or None
    # token_metadata = await fetch_token_metadata(existing)  # Will automatically refresh the metadata if existing is None or outdated.
    # save_token_metadata_to_storage(token_metadata)  # Persist this for future runs.
    #
    # client = PATHRealtimeClient(token_metadata)

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
