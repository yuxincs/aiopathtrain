#!/usr/bin/env python3
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
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final, Literal

import aiohttp
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from signalrcore.hub_connection_builder import HubConnectionBuilder

_LOGGER = logging.getLogger(__name__)

# These are the hardcoded keys from the PATH app
_CONFIG_DECRYPT_KEY: Final = "PVTG16QwdKSbQhjIwSsQdAm0i"
# bytes([73, 118, 97, 110, 32, 77, 101, 100, 118, 101, 100, 101, 118])
_KEY_SALT: Final = b"Ivan Medvedev"


def decrypt(cipher_text: str) -> str:
    """Decrypt base64-encoded AES-encrypted string"""
    # Decode base64
    buffer = base64.b64decode(cipher_text.replace(" ", "+"))

    # Derive key and IV using PBKDF2 (matching C# Rfc2898DeriveBytes)
    # C# calls GetBytes(32) then GetBytes(16) on the same instance
    key_and_iv = hashlib.pbkdf2_hmac("sha1", _CONFIG_DECRYPT_KEY.encode(), _KEY_SALT, 1000, 48)
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


Direction = Literal["New York", "New Jersey"]


@dataclass(frozen=True)
class TrainArrival:
    """Information about a train arriving at a station"""

    station: str
    direction: Direction
    seconds_to_arrival: int
    line_color: str
    headsign: str


class PathRealtimeClient:
    """Main client for PATH real-time data"""

    def __init__(self, existing_database_info: DatabaseInfo | None = None):
        self._database_info = existing_database_info

    @property
    def database_info(self) -> DatabaseInfo | None:
        return self._database_info

    async def listen(self, station: str, direction: Direction) -> AsyncIterator[TrainArrival]:
        """Connect to SignalR hub for real-time data for a station"""
        self._database_info = await _refresh_database_info(self._database_info)

        token_info = await self._get_signalr_token(station, direction)

        connection = (
            HubConnectionBuilder()
            .with_url(
                token_info["Url"],
                options={"access_token_factory": lambda: token_info["AccessToken"]},
            )
            .build()
        )

        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()

        def on_message(message: list[str]):
            if len(message) != 2:
                _LOGGER.warning("unexpected message format: %s", message)
                return

            _, json_data = message
            data: dict[str, object] = json.loads(json_data)
            if "messages" not in data:
                _LOGGER.warning("missing 'messages' field in message: %s", data)
                return

            messages = data["messages"]
            if not isinstance(messages, list):
                _LOGGER.warning("invalid 'messages' field in message: %s", data)
                return

            # Extract train arrival information
            for msg in messages:
                arrival = TrainArrival(
                    station=station,
                    direction=direction,
                    seconds_to_arrival=int(msg["secondsToArrival"]),
                    line_color=msg["lineColor"],
                    headsign=msg["headSign"],
                )
                loop.call_soon_threadsafe(queue.put_nowait, arrival)

        connection.on("SendMessage", on_message)
        connection.start()

        try:
            while True:
                yield await queue.get()
        except asyncio.exceptions.CancelledError:
            pass
        finally:
            connection.stop()

    async def _get_signalr_token(self, station: str, direction: str) -> dict[str, str]:
        """Get SignalR access token for a specific station and direction"""

        payload = {"station": station, "direction": direction}
        headers = {
            "Authorization": f"Bearer {self.database_info.token_value}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.database_info.token_broker_url,
                json=payload,
                headers=headers,
                raise_for_status=True,
            ) as response:
                return await response.json()


@dataclass(frozen=True)
class DatabaseInfo:
    """Information about the PATH database"""

    checksum: str
    token_broker_url: str
    token_value: str
    station_mappings: dict[str, str]


async def _refresh_database_info(database_info: DatabaseInfo | None = None) -> DatabaseInfo:
    """Refresh the database info from the PATH API"""
    base_url = "https://path-mppprod-app.azurewebsites.net/api/v1/"
    headers = {
        "apikey": "3CE6A27D-6A58-4CA5-A3ED-CE2EBAEFA166",
        "appname": "RidePATH",
        "appversion": "4.3.0",
        "user-agent": "okhttp/3.12.6",
    }

    checksum = database_info.checksum if database_info else "3672A87A4D8E9104E736C3F61023F013"
    async with aiohttp.ClientSession(base_url=base_url, headers=headers) as session:
        # Check the current remote database version.
        additional_headers = {"dbchecksum": checksum}
        async with session.get("Config/Fetch", headers=additional_headers) as response:
            # No update available.
            match response.status:
                case 404:
                    pass
                case 200:
                    checksum = (
                        (await response.json()).get("Data", {}).get("DbUpdate", {}).get("Checksum")
                    )
                case _:
                    response.raise_for_status()

        # If no update is needed.
        if database_info and checksum == database_info.checksum:
            return database_info

        async with session.get("file/clientdb", params={"checksum": checksum}) as response:
            response.raise_for_status()
            return _load_info_from_db(await response.read(), checksum)


def _load_info_from_db(db_data: bytes, checksum: str) -> DatabaseInfo:
    """Load token broker URL / token / station name mappings etc. from the database"""

    # Extract database from zip file.
    with zipfile.ZipFile(io.BytesIO(db_data)) as zf:
        db_filename = zf.namelist()[0]  # Should be the .db file
        db_data = zf.read(db_filename)

    with tempfile.NamedTemporaryFile(suffix=".db") as temp_file:
        temp_file.write(db_data)
        temp_file.flush()

        conn = sqlite3.connect(temp_file.name)
        cursor = conn.cursor()

        def config_value(key: str) -> str:
            return cursor.execute(
                "SELECT configuration_value FROM tblConfigurationData WHERE configuration_key = ?",
                (key,),
            ).fetchone()[0]

        token_broker_url = decrypt(config_value("rt_TokenBrokerUrl_Prod"))
        token_value = decrypt(config_value("rt_TokenValue_Prod"))

    # This would need to be extracted from the database structure. For now, using common PATH stations.
    mappings = {
        "Newark": "NWK",
        "Harrison": "HAR",
        "Journal Square": "JSQ",
        "Grove Street": "GRV",
        "Exchange Place": "EXP",
        "World Trade Center": "WTC",
        "Christopher Street": "CHR",
        "9th Street": "09S",
        "14th Street": "14S",
        "23rd Street": "23S",
        "33rd Street": "33S",
        "Hoboken": "HOB",
    }

    return DatabaseInfo(checksum, token_broker_url, token_value, mappings)
