"""Minimal synchronous client for the ESPHome native OTA protocol.

Faithful port of the upload path of esphome's espota2.py (app OTA only).
Blocking socket I/O - always run through an executor.
"""

from __future__ import annotations

from collections.abc import Callable
import gzip
import hashlib
import logging
import secrets
import socket

_LOGGER = logging.getLogger(__name__)

MAGIC_BYTES = bytes([0x6C, 0x26, 0xF7, 0x5C, 0x45])

RESPONSE_OK = 0x00
RESPONSE_REQUEST_AUTH = 0x01
RESPONSE_REQUEST_SHA256_AUTH = 0x02
RESPONSE_AUTH_OK = 0x41
RESPONSE_UPDATE_PREPARE_OK = 0x42
RESPONSE_BIN_MD5_OK = 0x43
RESPONSE_RECEIVE_OK = 0x44
RESPONSE_UPDATE_END_OK = 0x45
RESPONSE_SUPPORTS_COMPRESSION = 0x46
RESPONSE_CHUNK_OK = 0x47
RESPONSE_FEATURE_FLAGS = 0x48

CLIENT_FEATURE_SUPPORTS_COMPRESSION = 0x01
CLIENT_FEATURE_SUPPORTS_SHA256_AUTH = 0x02
CLIENT_FEATURE_SUPPORTS_EXTENDED_PROTOCOL = 0x04
SERVER_FEATURE_SUPPORTS_COMPRESSION = 0x01

OTA_TYPE_UPDATE_APP = 0x00

OTA_VERSION_2_0 = 2

UPLOAD_BLOCK_SIZE = 8192
UPLOAD_BUFFER_SIZE = UPLOAD_BLOCK_SIZE * 8

_ERROR_MESSAGES = {
    0x80: "Invalid magic byte",
    0x81: "Couldn't prepare flash memory for update (is the binary too big?)",
    0x82: "Authentication invalid",
    0x83: "Writing OTA data to flash memory failed",
    0x84: "Finishing update failed",
    0x85: "Manual reset required (first OTA after a serial flash)",
    0x86: "Device flashed with wrong flash size",
    0x87: "Device does not have the requested flash size",
    0x88: "Not enough space on ESP8266 to store the OTA image",
    0x89: "Not enough space on ESP32 to store the OTA image",
    0x8A: "No update partition",
    0x8B: "MD5 mismatch",
    0x8C: "Not enough space on RP2040 to store the OTA image",
    0x8D: "Firmware signature invalid (image not signed with the trusted key)",
    0x8E: "Unsupported OTA type",
    0x93: "Device rejected downgrade (OTA downgrade protection)",
    0xFF: "Unknown error from device",
}

_AUTH_METHODS = {
    RESPONSE_REQUEST_SHA256_AUTH: (hashlib.sha256, 64, "SHA256"),
    RESPONSE_REQUEST_AUTH: (hashlib.md5, 32, "MD5"),
}


class OTAError(Exception):
    """OTA push failed."""


def _receive_exactly(
    sock: socket.socket, amount: int, what: str, expect: list[int] | None
) -> bytes:
    """Receive exactly `amount` bytes, validating the first byte like espota2."""
    data = b""
    try:
        data += sock.recv(1)
    except OSError as err:
        raise OTAError(f"receiving {what}: {err}") from err
    if not data:
        raise OTAError(f"device closed connection while receiving {what}")
    code = data[0]
    if (msg := _ERROR_MESSAGES.get(code)) is not None:
        raise OTAError(f"{what}: {msg}")
    if expect is not None and code not in expect:
        raise OTAError(f"{what}: unexpected response 0x{code:02X}")
    while len(data) < amount:
        try:
            chunk = sock.recv(amount - len(data))
        except OSError as err:
            raise OTAError(f"receiving {what}: {err}") from err
        if not chunk:
            raise OTAError(f"device closed connection while receiving {what}")
        data += chunk
    return data


def _send(sock: socket.socket, data: bytes, what: str) -> None:
    try:
        sock.sendall(data)
    except OSError as err:
        raise OTAError(f"sending {what}: {err}") from err


def _perform_auth(
    sock: socket.socket, password: str | None, auth_code: int
) -> None:
    hash_func, nonce_size, hash_name = _AUTH_METHODS[auth_code]
    if not password:
        raise OTAError("device requests an OTA password, but none is configured")
    nonce = _receive_exactly(sock, nonce_size, f"{hash_name} auth nonce", None).decode()
    cnonce = secrets.token_hex(nonce_size // 2)
    _send(sock, cnonce.encode(), "auth cnonce")
    hasher = hash_func()
    hasher.update(password.encode("utf-8"))
    hasher.update(nonce.encode())
    hasher.update(cnonce.encode())
    _send(sock, hasher.hexdigest().encode(), "auth result")
    _receive_exactly(sock, 1, "auth result", [RESPONSE_AUTH_OK])


def push_firmware(
    host: str,
    port: int,
    password: str | None,
    image: bytes,
    progress: Callable[[float], None] | None = None,
) -> None:
    """Push an app firmware image to an ESPHome device (blocking)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    try:
        try:
            sock.connect((host, port))
        except OSError as err:
            raise OTAError(f"connecting to {host}:{port}: {err}") from err

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        _send(sock, MAGIC_BYTES, "magic bytes")

        version = _receive_exactly(sock, 2, "version", [RESPONSE_OK])[1]
        _LOGGER.debug("Device supports OTA version %s", version)

        _send(
            sock,
            bytes(
                [
                    CLIENT_FEATURE_SUPPORTS_COMPRESSION
                    | CLIENT_FEATURE_SUPPORTS_SHA256_AUTH
                    | CLIENT_FEATURE_SUPPORTS_EXTENDED_PROTOCOL
                ]
            ),
            "features",
        )
        features_resp = _receive_exactly(sock, 1, "features", None)[0]
        extended_proto = False
        if features_resp == RESPONSE_FEATURE_FLAGS:
            extended_proto = True
            server_features = _receive_exactly(sock, 1, "feature flags", None)[0]
        elif features_resp == RESPONSE_SUPPORTS_COMPRESSION:
            server_features = SERVER_FEATURE_SUPPORTS_COMPRESSION
        else:
            server_features = 0

        if server_features & SERVER_FEATURE_SUPPORTS_COMPRESSION:
            contents = gzip.compress(image, compresslevel=9)
            _LOGGER.debug("Compressed %d -> %d bytes", len(image), len(contents))
        else:
            contents = image

        auth = _receive_exactly(
            sock,
            1,
            "auth",
            [RESPONSE_REQUEST_AUTH, RESPONSE_REQUEST_SHA256_AUTH, RESPONSE_AUTH_OK],
        )[0]
        if auth != RESPONSE_AUTH_OK:
            _perform_auth(sock, password, auth)

        # Matches the device-side data-phase socket timeout.
        sock.settimeout(90.0)

        if extended_proto:
            _send(sock, bytes([OTA_TYPE_UPDATE_APP]), "ota type")

        size = len(contents)
        _send(sock, size.to_bytes(4, "big"), "binary size")
        _receive_exactly(sock, 1, "update prepare result", [RESPONSE_UPDATE_PREPARE_OK])

        _send(sock, hashlib.md5(contents).hexdigest().encode(), "file checksum")
        _receive_exactly(sock, 1, "file checksum result", [RESPONSE_BIN_MD5_OK])

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, UPLOAD_BUFFER_SIZE)

        offset = 0
        while offset < size:
            chunk = contents[offset : offset + UPLOAD_BLOCK_SIZE]
            offset += len(chunk)
            _send(sock, chunk, "data chunk")
            if version >= OTA_VERSION_2_0:
                _receive_exactly(sock, 1, "chunk result", [RESPONSE_CHUNK_OK])
            if progress is not None:
                progress(offset / size)

        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        _receive_exactly(sock, 1, "update receive result", [RESPONSE_RECEIVE_OK])
        _receive_exactly(sock, 1, "update end result", [RESPONSE_UPDATE_END_OK])
        try:
            _send(sock, bytes([RESPONSE_OK]), "end acknowledgement")
        except OTAError:
            # The device treats a missing final ack as non-fatal; it is
            # already committing and rebooting into the new firmware.
            _LOGGER.debug("End acknowledgement not delivered (device rebooting)")
        _LOGGER.info("OTA push to %s successful", host)
    finally:
        try:
            sock.close()
        except OSError:
            pass
