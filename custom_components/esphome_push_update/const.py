"""Constants for the ESPHome Push Update integration."""

from datetime import timedelta

DOMAIN = "esphome_push_update"

# Firmware uses passwordless native OTA.
OTA_PASSWORD: str | None = None

# ESP8266/ESP8285 listen on 8266, everything else on 3232.
OTA_PORT_ESP8266 = 8266
OTA_PORT_DEFAULT = 3232

UPDATE_INTERVAL = timedelta(minutes=30)
