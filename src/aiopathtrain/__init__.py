"""
PATH Real-Time API Client

This client directly accesses the PATH backend system to retrieve real-time train data.
Based on the blog post at: https://medium.com/@mrazza/programmatic-path-real-time-arrival-data-5d0884ae1ad6

The client implements the following workflow:
1. Check for PATH database updates
2. Download the latest SQLite database if needed
3. Extract SignalR connection details from the database
4. Connect to SignalR hubs for real-time data
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import logging
import sqlite3
import tempfile
import zipfile
from asyncio import Queue
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Final, Literal, TypedDict, cast

import aiohttp
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pysignalr.client import SignalRClient

_LOGGER = logging.getLogger(__name__)

Direction = Literal["New York", "New Jersey"]


@dataclass(frozen=True)
class TrainArrival:
    """Information about a train arriving at a station"""

    station: str
    direction: Direction
    seconds_to_arrival: int
    line_color: str
    headsign: str


class _MessageData(TypedDict):
    messages: list[dict[str, str]]


class PATHRealtimeClient:
    """Main client for PATH real-time data"""

    def __init__(self, token_metadata: TokenMetadata):
        self._token_metadata: TokenMetadata = token_metadata

    async def listen(
        self, station: str, direction: Direction, *, session: aiohttp.ClientSession | None = None
    ) -> AsyncIterator[TrainArrival]:
        """Connect to SignalR hub for real-time data for a station"""
        token_info = await self._token_metadata.signalr_token(station, direction, session=session)

        message_queue: Queue[list[str]] = Queue()

        url, access_token = token_info["Url"], token_info["AccessToken"]
        # The PATH SignalR hub requires the access token to be set even during the "negotiate" stage,
        # so here we set the token in the headers, in addition to the access_token_factory.
        headers = {"Authorization": f"Bearer {access_token}"}
        client = SignalRClient(url, access_token_factory=lambda: access_token, headers=headers)
        client.on("SendMessage", message_queue.put)

        # Now, start the client in the _background_, the messages will be pushed to the queue,
        # and we will be reading the queue here in the main thread.
        task = asyncio.create_task(client.run())

        try:
            while True:
                message = await message_queue.get()

                # Each message is in this format: [metadata, json_string].
                _, json_data = message
                data = cast(_MessageData, json.loads(json_data))

                for msg in data["messages"]:
                    yield TrainArrival(
                        station=station,
                        direction=direction,
                        seconds_to_arrival=int(msg["secondsToArrival"]),
                        line_color=msg["lineColor"],
                        headsign=msg["headSign"],
                    )
        finally:
            # Always make sure to stop the client when we are done.
            task.cancel()
            await task


@dataclass(frozen=True)
class TokenMetadata:
    """Information about the PATH database"""

    checksum: str
    token_broker_url: str
    token_value: str

    async def signalr_token(
        self, station: str, direction: Direction, *, session: aiohttp.ClientSession | None = None
    ) -> dict[str, str]:
        """Get SignalR access token for a specific station and direction"""
        payload: dict[str, str] = {"station": station, "direction": direction}
        headers: dict[str, str] = {
            "Authorization": f"Bearer {self.token_value}",
            "Content-Type": "application/json",
        }

        async with AsyncExitStack() as stack:
            if not session:
                session = aiohttp.ClientSession()
                await stack.enter_async_context(session)

            async with session.post(
                self.token_broker_url, json=payload, headers=headers, raise_for_status=True
            ) as response:
                return cast(dict[str, str], await response.json())


async def fetch_token_metadata(
    existing: TokenMetadata | None = None, *, session: aiohttp.ClientSession | None = None
) -> TokenMetadata:
    """Fetch the token metadata from the PATH backend system.

    If an existing metadata object is provided, it will be checked for updates and only refreshed if needed.
    """
    base_url = "https://path-mppprod-app.azurewebsites.net/api/v1/"
    headers: dict[str, str] = {
        "apikey": "3CE6A27D-6A58-4CA5-A3ED-CE2EBAEFA166",
        "appname": "RidePATH",
        "appversion": "4.3.0",
        "user-agent": "okhttp/3.12.6",
    }

    checksum = existing.checksum if existing else "3672A87A4D8E9104E736C3F61023F013"

    async with AsyncExitStack() as stack:
        if not session:
            session = aiohttp.ClientSession(base_url=base_url, headers=headers)
            await stack.enter_async_context(session)

        # Check the current remote database version.
        additional_headers = {"dbchecksum": checksum}
        async with session.get("Config/Fetch", headers=additional_headers) as response:
            if response.status == 200:
                data = cast(dict[str, dict[str, dict[str, str]]], await response.json())
                checksum = data.get("Data", {}).get("DbUpdate", {}).get("Checksum")
                if not checksum:
                    raise RuntimeError(f"No checksum returned from PATH backend: {data}")
            elif response.status != 404:  # 404 means no update is needed.
                response.raise_for_status()

        # If no update is needed.
        if existing and checksum == existing.checksum:
            return existing

        params: dict[str, str] = {"checksum": checksum}
        async with session.get("file/clientdb", params=params, raise_for_status=True) as response:
            db_data = await response.read()

    # The database is a zip file containing a single SQLite database.
    with zipfile.ZipFile(io.BytesIO(db_data)) as zf:
        db_filename = zf.namelist()[0]  # Should be the .db file
        db_data = zf.read(db_filename)

    with tempfile.NamedTemporaryFile(suffix=".db") as temp_file:
        temp_file.write(db_data)
        temp_file.flush()

        sql_code = (
            "SELECT configuration_value FROM tblConfigurationData WHERE configuration_key = ?"
        )

        with sqlite3.connect(temp_file.name) as conn:

            def config_value(key: str) -> str:
                return cast(str, conn.execute(sql_code, (key,)).fetchone()[0])

            token_broker_url = _decrypt(config_value("rt_TokenBrokerUrl_Prod"))
            token_value = _decrypt(config_value("rt_TokenValue_Prod"))

    return TokenMetadata(
        checksum=checksum, token_broker_url=token_broker_url, token_value=token_value
    )


def _decrypt(cipher_text: str) -> str:
    """Decrypt base64-encoded AES-encrypted string"""
    # These are the hardcoded keys from the PATH app
    config_encryption_key: Final = b"PVTG16QwdKSbQhjIwSsQdAm0i"
    key_salt: Final = b"Ivan Medvedev"

    # Decode base64
    buffer = base64.b64decode(cipher_text.replace(" ", "+"))

    # Derive key and IV using PBKDF2 (matching C# Rfc2898DeriveBytes)
    # C# calls GetBytes(32) then GetBytes(16) on the same instance
    key_and_iv = hashlib.pbkdf2_hmac("sha1", config_encryption_key, key_salt, 1000, 48)
    key = key_and_iv[:32]
    iv = key_and_iv[32:48]

    # Decrypt
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(buffer) + decryptor.finalize()

    # Remove PKCS7 padding and decode UTF-16
    # PKCS7 padding: last byte indicates number of padding bytes
    padding_length = decrypted[-1]
    decrypted = decrypted[:-padding_length]

    # Decode UTF-16LE and clean up any null characters
    result = decrypted.decode("utf-16le").rstrip("\x00")
    return result
