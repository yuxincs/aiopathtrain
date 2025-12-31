# Async PATH train client

Asynchronous Python client for the Port Authority Trans-Hudson (PATH) real-time feed. This client
library reproduces the workflow of the official RidePATH app and subscribes to the live arrival
streams for any station.

This is a client port of [mrazza/path-data](https://github.com/mrazza/path-data) based on their
amazing reverse-engineering work outlined in this
[blog post](https://medium.com/@mrazza/programmatic-path-real-time-arrival-data-5d0884ae1ad6).

<details>
  <summary>Why not use https://www.panynj.gov/bin/portauthority/ridepath.json </summary>
  
> I have generally found the data provided by the PATH HTTP API to be inaccurate
> (often off by 2 or 3 minutes). The live-stream approach used in this repository is much more accurate (errors are usually within seconds).
</details>

This software is not endorsed nor supported by the Port Authority of New York and New Jersey.

## Getting started

Install the package from PyPI:

```bash
$ pip install aiopathtrain
```

The main API is `aiopathtrain.PATHRealtimeClient().listen(station, direction)`, which returns an
async iterator over
`Arrival` objects, each representing a train arrival update message for the specified station and
direction.

```python
import asyncio

from aiopathtrain import PATHRealtimeClient


async def main():
    client = PATHRealtimeClient()
    # Or fetch and persist the token metadata for faster initialization (if the metadata is still valid):
    #
    # existing = load_token_metadata_from_storage() or None
    # token_metadata = await aiopathtrain.fetch_token_metadata(existing)  # Will automatically refresh the metadata if existing is None or outdated.
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
```

A demo script is included in the repository that can be run with `python -m aiopathtrain`.

## License

[MIT](https://github.com/yuxincs/aiopathtrain/blob/main/LICENSE)
