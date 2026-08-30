"""Dynamically created update entities that push firmware over native OTA."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PushUpdateConfigEntry
from .const import OTA_PASSWORD
from .coordinator import DeviceUpdateInfo, PushUpdateCoordinator
from .espota import OTAError, push_firmware

_LOGGER = logging.getLogger(__name__)


def _esphome_device_for(
    hass: HomeAssistant, esphome_entry_id: str
) -> dr.DeviceEntry | None:
    """The ESPHome integration's main device entry for a config entry.

    The entity attaches to this existing device by setting `device_entry`
    directly (the switch_as_x pattern). Device-registry matching is scoped
    per config entry since HA 2026, so registering our own DeviceInfo with
    the same MAC would create a separate nameless device instead of linking.
    """
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, esphome_entry_id)
    for device in devices:
        # Main device (ESPHome sub-devices hang off it via via_device_id).
        if device.via_device_id is None and device.connections:
            return device
    return next(iter(devices), None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PushUpdateConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync_entities() -> None:
        new = [
            PushUpdateEntity(
                coordinator,
                esphome_entry_id,
                _esphome_device_for(hass, esphome_entry_id),
            )
            for esphome_entry_id in (coordinator.data or {})
            if esphome_entry_id not in known
        ]
        known.update(e.esphome_entry_id for e in new)
        if new:
            async_add_entities(new)

    _sync_entities()
    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))


class PushUpdateEntity(CoordinatorEntity[PushUpdateCoordinator], UpdateEntity):
    """Firmware update entity backed by an HA-side OTA push."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )
    _attr_has_entity_name = True
    _attr_name = "Firmware"

    def __init__(
        self,
        coordinator: PushUpdateCoordinator,
        esphome_entry_id: str,
        device_entry: dr.DeviceEntry | None,
    ) -> None:
        super().__init__(coordinator)
        self.esphome_entry_id = esphome_entry_id
        self._attr_unique_id = f"{esphome_entry_id}_pushed_firmware"
        if device_entry is not None:
            self.device_entry = device_entry
        self._installed_override: str | None = None

    @property
    def _info(self) -> DeviceUpdateInfo | None:
        return (self.coordinator.data or {}).get(self.esphome_entry_id)

    @property
    def available(self) -> bool:
        return super().available and self._info is not None

    @property
    def state(self) -> str | None:
        """Offer any published release to a device running a dev build.

        HA's default AwesomeVersion comparison considers a release tag NOT
        newer than the "dev" that locally-built firmware reports and would
        show "up to date". Only that case is special-cased; release-to-release
        comparisons keep HA's semantic versioning behavior (so a device on a
        newer release than the manifest is not offered a downgrade).
        """
        installed = self.installed_version
        if installed == "dev":
            latest = self.latest_version
            if latest is None:
                return None
            return STATE_ON if latest != installed else STATE_OFF
        return super().state

    @property
    def installed_version(self) -> str | None:
        """Project version reported live by the ESPHome integration."""
        esphome_entry = self.hass.config_entries.async_get_entry(
            self.esphome_entry_id
        )
        runtime = getattr(esphome_entry, "runtime_data", None)
        device_info = getattr(runtime, "device_info", None)
        return getattr(device_info, "project_version", None) or self._installed_override

    @property
    def latest_version(self) -> str | None:
        return self._info.latest_version if self._info else None

    @property
    def title(self) -> str | None:
        return self._info.manifest_name if self._info else None

    @property
    def release_summary(self) -> str | None:
        # HA limits release_summary to 255 chars; full text is available via
        # the release-notes dialog (RELEASE_NOTES feature).
        summary = self._info.summary if self._info else None
        if summary and len(summary) > 255:
            return summary[:252] + "..."
        return summary

    async def async_release_notes(self) -> str | None:
        """Full release notes (markdown) for the update dialog."""
        return self._info.summary if self._info else None

    @property
    def release_url(self) -> str | None:
        return self._info.release_url if self._info else None

    @callback
    def _set_progress(self, fraction: float) -> None:
        self._attr_update_percentage = round(fraction * 100)
        self.async_write_ha_state()

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        info = self._info
        if info is None:
            raise HomeAssistantError("No manifest data available for this device")

        session = async_get_clientsession(self.hass)
        try:
            async with session.get(info.ota_url, timeout=120) as resp:
                resp.raise_for_status()
                image = await resp.read()
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Downloading firmware failed: {err}") from err

        if info.ota_md5 and hashlib.md5(image).hexdigest() != info.ota_md5:
            raise HomeAssistantError(
                "Downloaded firmware does not match the manifest MD5; aborting"
            )

        self._attr_in_progress = True
        self._attr_update_percentage = 0
        self.async_write_ha_state()

        loop = self.hass.loop

        def progress(fraction: float) -> None:
            loop.call_soon_threadsafe(self._set_progress, fraction)

        try:
            await self.hass.async_add_executor_job(
                push_firmware,
                info.host,
                info.ota_port,
                OTA_PASSWORD,
                image,
                progress,
            )
        except OTAError as err:
            raise HomeAssistantError(f"OTA push failed: {err}") from err
        finally:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            self.async_write_ha_state()

        # The device reboots and the ESPHome integration reports the new
        # project version shortly; show it optimistically meanwhile.
        self._installed_override = info.latest_version
        self.async_write_ha_state()
