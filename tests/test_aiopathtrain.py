from aiopathtrain import Direction, PATHRealtimeClient


async def test_path_train():
    client = PATHRealtimeClient()

    station = "Exchange Place"
    direction: Direction = "New York"
    async for arrival in client.listen(station, direction):
        assert arrival.station == station and arrival.direction == direction
        assert arrival.seconds_to_arrival >= 0
        assert arrival.line_color
        assert arrival.headsign
        break  # A successful delivery of a single message is good enough - we stop here.
