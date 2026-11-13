# SmartIR(Mod)

[![Lint](https://github.com/raphael1688dev/SmartIR_Mod/actions/workflows/lint.yml/badge.svg)](https://github.com/raphael1688dev/SmartIR_Mod/actions/workflows/lint.yml)

A fork of [smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR) modernised for Home Assistant **2026.11+**, with Config Flow setup, multi-HA state sync over MQTT, and several pre-existing bugs fixed.

> ⚠️ This fork respects the upstream project's name guideline. It is published under the distinct name **SmartIR(Mod)**, not "SmartIR".

---

## What's different from upstream

| Area | Upstream SmartIR | This fork (Mod) |
|---|---|---|
| HA compatibility | Older HA versions | **HA 2026.11+ only** |
| Setup | YAML platform setup | **Config Flow (UI) + YAML soft-import** |
| Updates | Built-in self-updater | Removed — use Home Assistant's Marketplace (formerly HACS) |
| Bug fixes | — | `hass.components.*` removed, `async_add_executor_job` kwargs, `@callback`/`async def`, `Helper.downloader` signature, `swing_mode` restore validation, full restore-state hardening across all platforms, HA 2026.10 entity contract (`@final` state, light color modes, no polling), HA 2026.11 climate `native_*` temperatures, 3 unparseable device JSONs |
| Multi-HA support | Each HA's state drifts independently | **Optional MQTT intent topic** for cross-HA state sync (topic derived from physical device identity, not entity name) |
| codes source | Downloads from `smartHomeHub/SmartIR` | Downloads from this fork (`raphael1688dev/SmartIR_Mod`) — different `supportedController` values for many device codes |

See [CHANGELOG.md](CHANGELOG.md) for every change by version.

---

## Requirements

- Home Assistant **2026.11.0+** (checked against 2026.11.0 and 2026.11.1). The last release that runs on 2026.5–2026.10 is `20261110r0`.
- The Marketplace built into Home Assistant (recommended for installation/updates)
- For the MQTT controller: HA MQTT integration set up and connected to your broker (e.g., Mosquitto)
- For multi-HA intent sync: MQTT integration on each HA instance, sharing the same broker

---

## Installation

### Marketplace (recommended)

Home Assistant 2026.11 replaced HACS with the built-in **Marketplace**. It reads the same `hacs.json` and GitHub releases, and an existing HACS installation (including its custom repositories) is taken over when Home Assistant is upgraded.

1. In the Marketplace, add `https://github.com/raphael1688dev/SmartIR_Mod` as a custom repository with category **Integration**
2. Install **SmartIR(Mod)**
3. Restart Home Assistant

Pre-releases only show up when the repository's **Pre-release** switch is on.

### Manual

Copy `custom_components/smartir/` into your Home Assistant `config/custom_components/` directory, then restart HA.

> Device-code JSON files are **not** bundled — they're downloaded on first use of each `device_code` from this fork's `codes/` directory. The `persistent_directory: codes` setting in [hacs.json](hacs.json) keeps downloaded files across Marketplace updates.

---

## Setup

### Via UI (Config Flow) — recommended

1. Settings → **Devices & Services** → **+ Add Integration** → search **SmartIR**
2. Pick the platform: Climate / Media Player / Fan / Light
3. Fill in the form:
   - **Name** — entity name shown in HA
   - **Device code** — number identifying the IR code set (see device-code lists in [docs/](docs/))
   - **Controller data** — depends on controller type (see [Supported controllers](#supported-controllers))
   - **Delay** — seconds between commands (default 0.5)
   - Optional: temperature / humidity / power sensors
4. Submit

To edit settings later (sensors, delay, multi-HA sync): the integration card → ⚙️ **Options**.

The setup and options forms are translated into English and Traditional Chinese.

### Via YAML (legacy)

YAML setup still works and **auto-imports** to a Config Entry on first run. Existing configurations from the upstream SmartIR migrate transparently.

```yaml
climate:
  - platform: smartir
    name: "Living Room AC"
    unique_id: living_room_ac
    device_code: 1080
    controller_data: "zigbee2mqtt/0xXXXX/set"     # MQTT topic
    temperature_sensor: sensor.living_room_temp
    humidity_sensor: sensor.living_room_humid
    power_sensor: binary_sensor.living_room_ac_power
    power_sensor_restore_state: true
```

After the first restart, a Config Entry is created and a deprecation warning will log; once you've verified the entity works, remove the YAML block — the entity continues running from the Config Entry. `unique_id` is preserved so `entity_id` stays the same.

---

## Supported controllers

`supportedController` value in the device JSON determines the wire protocol. The `controller_data` field in your config means different things for each:

| Controller | `controller_data` example | Notes |
|---|---|---|
| **Broadlink** | `remote.broadlink_living_room` | HA remote entity id |
| **Xiaomi** | `remote.xiaomi_miio_xxx` | HA remote entity id |
| **MQTT** | `zigbee2mqtt/0xXXXX/set` | MQTT topic; payload format depends on device (e.g., `{"ir_code_to_send": "..."}` for Tuya UFO-R11 via Zigbee2MQTT) |
| **LOOKin** | `192.168.1.100` | LOOKin device IP / hostname |
| **ESPHome** | `<service_name>` | ESPHome user-defined service |

Encoding (`commandsEncoding` in device JSON): `Raw`, `Base64`, `Hex`, or `Pronto` depending on controller.

---

## Multi-HA state sync (optional)

When two or more HA instances share the same physical IR device (e.g., a vacation home + main home, or redundant HAs), they previously drifted into inconsistent state because IR is unidirectional and Z2M's IR blasters don't echo sent codes back.

This fork adds an **opt-in MQTT "intent" topic**: after each successful IR command, the entity publishes its current state (retained) to `<topic_base>/<intent_id>`. "Successful" means the controller accepted the command: the MQTT and LOOKin controllers report a failed send (and nothing is published), while Broadlink, Xiaomi and ESPHome commands are handed to their Home Assistant service without waiting for the result. Other HA instances subscribe and update their entity state — **without re-sending IR** (loop-prevented via per-entry UUID).

### Topic identity (`intent_id`)

The topic suffix is derived from the **physical device identity**, not the entity name or HA-assigned `unique_id`:

```
intent_id = f"{platform}_{device_code}_{slug(controller_data)}"
```

Example for climate device code 1090 on the MQTT topic `zigbee2mqtt_3F/0xb0c7defffe5f308e/set`:
```
smartir/intent/climate_1090_zigbee2mqtt_3f_0xb0c7defffe5f308e_set
```

This means **HA1 and HA2 produce the same intent topic for the same physical device** as long as they share `device_code` and `controller_data` (which they must, to control the same device). Entity names can legitimately differ across instances (e.g., "Master AC" on one HA and "Bedroom AC" on the other) — only the physical identity matters.

### Enabling

For each device, on each HA instance:
1. Settings → Devices & Services → SmartIR → the device entry → ⚙️ Options
2. Enable **Sync state across multiple HA instances via MQTT**
3. Optionally change the topic base (default `smartir/intent`)
4. Submit; the entry reloads automatically

Requires the HA MQTT integration to be configured and pointing at the same broker on all instances. The integration declares `after_dependencies: ["mqtt"]` and runtime-waits for the MQTT client to become available before subscribing — no manual ordering needed.

### Caveats

- IR remote control by a physical remote is **not** synchronised (IR is one-way; no reverse channel).
- If MQTT is disconnected on an HA, that HA misses updates until reconnection; on reconnect, the retained intent is delivered immediately, so it catches up.
- All HAs must run the same major version of this integration to keep payload schemas compatible.
- If you change `controller_data` (e.g., move device to a new MQTT topic / new remote), the `intent_id` changes too, leaving the old retained message orphaned on the broker. Clear it with `mosquitto_pub -h <broker> -r -n -t '<old_topic>'` if desired.

---

## Per-platform documentation

Device-specific options and full device-code lists (inherited from upstream):
- [Climate](docs/CLIMATE.md)
- [Media Player](docs/MEDIA_PLAYER.md)
- [Fan](docs/FAN.md)
- [Light](docs/LIGHT.md)

> Note: upstream docs predate this fork's Config Flow. YAML examples there still work (auto-imported), but new setups should use the UI.

---

## Contributing device codes

New device JSONs go under `codes/<platform>/<device_code>.json` in this fork. The integration downloads them on demand from this repository's `main` branch — no integration update needed when new codes are added.

---

## Development

Tests live in `tests/` and run against a real Home Assistant package, so the same suite covers every supported HA version (CI runs the minimum, 2026.11.0, and the latest, 2026.11.1):

```bash
python3.14 -m venv .venv
.venv/bin/pip install homeassistant==2026.11.1 -r tests/requirements.txt
.venv/bin/python -m pytest
```

They build an entity from every device code file and check behaviour that has regressed before: sensor listeners released on reload, failed sends not changing climate state or publishing multi-HA intents, intents carrying the new state, and options that can clear a sensor. They do not talk to real IR hardware or an MQTT broker.

---

## Credits

Based on [smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR) by `@smartHomeHub` and contributors. The IR code-handling logic (`pronto2lirc`, `lirc2broadlink`, controller protocols) and most device JSONs are derived from upstream.

Fork modifications by [@raphael1688dev](https://github.com/raphael1688dev).

---

## License

MIT — see [LICENSE](LICENSE).
