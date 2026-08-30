"""Discovery + manifest polling for ESPHome Push Update.

Scans loaded ESPHome config entries. A device is eligible when it publishes
its own firmware manifest URL (a text sensor with object id
`firmware_manifest_url`, see packages/push-updates.yaml) and does NOT already
expose an on-device update entity - devices that self-update are left alone,
so no duplicate install buttons appear. No naming conventions and no
hardcoded URLs: the device tells HA where its firmware lives.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from urllib.parse import urljoin

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, OTA_PORT_DEFAULT, OTA_PORT_ESP8266, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

MANIFEST_URL_OBJECT_ID = "firmware_manifest_url"


@dataclass
class DeviceUpdateInfo:
    """Everything needed to show and perform a pushed update for one device."""

    esphome_entry_id: str
    device_name: str
    host: str
    ota_port: int
    manifest_name: str
    latest_version: str
    ota_url: str
    ota_md5: str | None
    summary: str | None
    release_url: str | None


def _manifest_url_for_entry(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the manifest URL the device publishes, if any."""
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if not (
            reg_entry.unique_id.endswith(MANIFEST_URL_OBJECT_ID)
            or (reg_entry.original_name or "").lower() == "firmware manifest url"
        ):
            continue
        state = hass.states.get(reg_entry.entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        url = state.state
        return url if url.startswith(("http://", "https://")) else None
    return None


def _has_native_update_entity(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    registry = er.async_get(hass)
    return any(
        e.domain == "update"
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
    )


class PushUpdateCoordinator(DataUpdateCoordinator[dict[str, DeviceUpdateInfo]]):
    """Rescans ESPHome entries and fetches manifests for eligible devices."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, DeviceUpdateInfo]:
        session = async_get_clientsession(self.hass)
        result: dict[str, DeviceUpdateInfo] = {}

        for entry in self.hass.config_entries.async_entries("esphome"):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            host = entry.data.get("host")
            if not host:
                continue
            manifest_url = _manifest_url_for_entry(self.hass, entry)
            if manifest_url is None:
                continue
            if _has_native_update_entity(self.hass, entry):
                continue  # the firmware self-updates; leave it alone

            try:
                async with session.get(manifest_url, timeout=30) as resp:
                    resp.raise_for_status()
                    manifest = await resp.json(content_type=None)
                build = next(b for b in manifest["builds"] if b.get("ota"))
                ota = build["ota"]
            except Exception as err:  # noqa: BLE001 - keep other devices working
                _LOGGER.warning("Fetching manifest for %s failed: %s", entry.title, err)
                # Keep the previous data for this device if we had any.
                if self.data and entry.entry_id in self.data:
                    result[entry.entry_id] = self.data[entry.entry_id]
                continue

            runtime = getattr(entry, "runtime_data", None)
            device_info = getattr(runtime, "device_info", None)
            model = (getattr(device_info, "model", "") or "").lower()
            ota_port = (
                OTA_PORT_ESP8266
                if "8266" in model or "8285" in model
                else OTA_PORT_DEFAULT
            )

            result[entry.entry_id] = DeviceUpdateInfo(
                esphome_entry_id=entry.entry_id,
                device_name=entry.title,
                host=host,
                ota_port=ota_port,
                manifest_name=manifest.get("name", entry.title),
                latest_version=str(manifest["version"]),
                ota_url=urljoin(manifest_url, ota["path"]),
                ota_md5=ota.get("md5"),
                summary=ota.get("summary") or None,
                release_url=ota.get("release_url") or None,
            )

        return result
