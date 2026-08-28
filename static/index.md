# About

Prebuilt, universal ESPHome firmware for my devices. No credentials are baked
in - after installing, provision Wi-Fi via Improv (BLE or USB) or by connecting
to the device's fallback hotspot, then adopt the device in the ESPHome
dashboard / Home Assistant. Installed devices check this site for OTA updates
automatically.

# Installation

Connect the device via USB and use the matching button below to install the
latest firmware directly from the browser (Chrome/Edge).

## Nous A5T power strip

Wi-Fi smart power strip with 3 individually switchable sockets, 3 USB ports
and energy monitoring ([product page](https://nous.technology/product/a5t.html)).

<esp-web-install-button manifest="firmware/nous-a5t.manifest.json"></esp-web-install-button>

## Ulanzi TC001 pixel clock

Desktop smart clock with a 32x8 RGB LED matrix, buzzer, light and
temperature/humidity sensors
([product page](https://www.ulanzi.de/en/products/ulanzi-pixel-smart-watch-2882)).

<esp-web-install-button manifest="firmware/ulanzi-tc001.manifest.json"></esp-web-install-button>

## SIM800L SMS gateway

ESP32-WROVER-B + SIM800L board (LILYGO T-Call v1.3 compatible): SMS in/out,
incoming-call caller ID and ring notifications, driven from Home Assistant.

<esp-web-install-button manifest="firmware/sim800l-gateway.manifest.json"></esp-web-install-button>

## Seeed XIAO Smart IR Mate

Compact infrared remote hub for Home Assistant with 360° IR emitters, IR
learning, touch pad and vibration feedback
([product page](https://www.seeedstudio.com/XIAO-Smart-IR-Mate-p-6492.html),
[wiki](https://wiki.seeedstudio.com/XIAO_IR_Mate_Smart_IR_Remote/)).

<esp-web-install-button manifest="firmware/xiao-ir-mate.manifest.json"></esp-web-install-button>

<script type="module" src="https://unpkg.com/esp-web-tools@10/dist/web/install-button.js?module"></script>
