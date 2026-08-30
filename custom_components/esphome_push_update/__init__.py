"""ESPHome Push Update.

Zero-configuration: one config entry discovers every ESPHome device that
advertises a firmware manifest URL but has no on-device update entity
(devices that cannot self-update, e.g. 1MB-flash ESP8266). Each gets an
HA-side update entity; installing downloads the image, verifies it and
pushes it over the native ESPHome OTA protocol. Installation always requires
the user pressing Install - nothing is flashed automatically.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .coordinator import MANIFEST_URL_OBJECT_ID, PushUpdateCoordinator

PLATFORMS = [Platform.UPDATE]

# Give a freshly connected device a moment to publish the sensor state
# before rescanning.
DISCOVERY_GRACE_SECONDS = 10

type PushUpdateConfigEntry = ConfigEntry[PushUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: PushUpdateConfigEntry) -> bool:
    coordinator = PushUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _rescan(_now) -> None:
        await coordinator.async_request_refresh()

    # ESPHome entries load in parallel with this one at boot; rescan once
    # everything is up so devices are not missed until the next interval.
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _rescan)
    )

    # Instant discovery: when a device exposing a manifest-URL sensor is
    # added to HA, its entity registration triggers a rescan shortly after.
    @callback
    def _entity_registry_updated(event: Event) -> None:
        if event.data.get("action") != "create":
            return
        entity_id: str = event.data.get("entity_id", "")
        if not entity_id.endswith(MANIFEST_URL_OBJECT_ID):
            return
        cancel = async_call_later(hass, DISCOVERY_GRACE_SECONDS, _rescan)
        entry.async_on_unload(cancel)

    entry.async_on_unload(
        hass.bus.async_listen(
            er.EVENT_ENTITY_REGISTRY_UPDATED, _entity_registry_updated
        )
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PushUpdateConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
