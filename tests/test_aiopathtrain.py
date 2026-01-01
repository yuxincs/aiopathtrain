from aiopathtrain import Direction, PATHRealtimeClient, fetch_token_metadata


async def test_path_train():
    token_metadata = await fetch_token_metadata()
    client = PATHRealtimeClient(token_metadata)

    station = "Exchange Place"
    direction: Direction = "New York"
    async for arrival in client.listen(station, direction):
        assert arrival.station == station and arrival.direction == direction
        assert arrival.seconds_to_arrival >= 0
        assert arrival.line_color
        assert arrival.headsign
        break  # A successful delivery of a single message is good enough - we stop here.
