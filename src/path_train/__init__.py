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
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from signalrcore.hub_connection_builder import HubConnectionBuilder

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


class PathApiClient:
    """Client for the PATH RESTful API"""

    def __init__(self):
        self.base_url = "https://path-mppprod-app.azurewebsites.net/api/v1"
        self.api_key = "3CE6A27D-6A58-4CA5-A3ED-CE2EBAEFA166"
        self.app_name = "RidePATH"
        self.app_version = "4.3.0"
        self.user_agent = "okhttp/3.12.6"
        self.session = requests.Session()

    def _get_headers(self) -> dict[str, str]:
        """Get standard headers for API requests"""
        return {
            "apikey": self.api_key,
            "appname": self.app_name,
            "appversion": self.app_version,
            "user-agent": self.user_agent,
        }

    def check_db_update(self, current_checksum: str) -> str | None:
        """Check if there's a database update available"""
        url = f"{self.base_url}/Config/Fetch"
        headers = self._get_headers()
        headers["dbchecksum"] = current_checksum

        response = self.session.get(url, headers=headers)

        if response.status_code == 404:
            return None  # No update available
        elif response.status_code == 200:
            data = response.json()
            return data.get("Data", {}).get("DbUpdate", {}).get("Checksum")
        else:
            response.raise_for_status()

    def download_database(self, checksum: str) -> bytes:
        """Download the PATH database with the given checksum"""
        url = f"{self.base_url}/file/clientdb"
        params = {"checksum": checksum}
        headers = self._get_headers()

        response = self.session.get(url, params=params, headers=headers)
        response.raise_for_status()
        return response.content


class PathRealtimeClient:
    """Main client for PATH real-time data"""

    def __init__(self, initial_checksum: str = "3672A87A4D8E9104E736C3F61023F013"):
        self.api_client = PathApiClient()
        self.realtime_data = {}

        # Download the database and check for updates.
        new_checksum = self.api_client.check_db_update(initial_checksum)
        db_data = self.api_client.download_database(new_checksum or initial_checksum)
        self.database_info = _load_info_from_db(db_data)

    async def connect_to_station(self, station: str, direction: str = "New York"):
        """Connect to SignalR hub for real-time data for a station"""
        print(f"Connecting to {station} ({direction})...")

        # Get SignalR token
        token_info = await self._get_signalr_token(station, direction)

        # Create SignalR connection
        connection = (
            HubConnectionBuilder()
            .with_url(
                token_info["Url"],
                options={"access_token_factory": lambda: token_info["AccessToken"]},
            )
            .build()
        )

        # Set up message handler
        def on_message(message):
            print(f"\n🚂 Received message for {station} ({direction}):")
            self._process_realtime_message(station, direction, message)

        connection.on("SendMessage", on_message)

        # Start connection
        connection.start()
        print(f"✅ Connected to {station} ({direction})")

        return connection

    async def _get_signalr_token(self, station: str, direction: str) -> dict[str, str]:
        """Get SignalR access token for a specific station and direction"""

        payload = {"station": station, "direction": direction}
        headers = {
            "Authorization": f"Bearer {self.database_info.token_value}",
            "Content-Type": "application/json",
        }

        response = requests.post(
            self.database_info.token_broker_url, json=payload, headers=headers
        )
        response.raise_for_status()

        return response.json()

    def _process_realtime_message(self, station: str, direction: str, message):
        """Process incoming real-time message"""
        try:
            # Message comes as a list: [metadata, json_data]
            if isinstance(message, list) and len(message) >= 2:
                json_data = message[1]
            else:
                json_data = message

            data = json.loads(json_data)

            # Extract train arrival information
            arrivals = []
            for msg in data.get("messages", []):
                arrival = {
                    "station": station,
                    "direction": direction,
                    "headsign": msg.get("headSign"),
                    "seconds_to_arrival": int(msg.get("secondsToArrival", 0)),
                    "arrival_message": msg.get("arrivalTimeMessage"),
                    "line_colors": msg.get("lineColor", "").split(","),
                    "last_updated": msg.get("lastUpdated"),
                }
                arrivals.append(arrival)

            # Store the data
            key = f"{station}_{direction}"
            self.realtime_data[key] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "arrivals": arrivals,
            }

            # Display the trains
            print(f"📍 {station} → {direction} ({len(arrivals)} trains)")
            for arrival in arrivals:
                mins = arrival["seconds_to_arrival"] // 60
                secs = arrival["seconds_to_arrival"] % 60
                if arrival["seconds_to_arrival"] == 0:
                    time_str = "NOW"
                else:
                    time_str = f"{mins}m {secs}s"
                print(f"  🚊 {arrival['headsign']}: {time_str} ({arrival['arrival_message']})")
            print()  # Add blank line

        except Exception as e:
            print(f"❌ Error processing message for {station} ({direction}): {e}")
            if isinstance(message, list):
                print(
                    f"Raw message (list): {message[1][:200]}..."
                    if len(message) > 1
                    else str(message)
                )


@dataclass(frozen=True)
class _DatabaseInfo:
    """Information about the PATH database"""

    token_broker_url: str
    token_value: str
    station_mappings: dict[str, str]


def _load_info_from_db(db_data: bytes) -> _DatabaseInfo:
    """Load token broker URL / token / station name mappings etc from the database"""

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

    return _DatabaseInfo(token_broker_url, token_value, mappings)
