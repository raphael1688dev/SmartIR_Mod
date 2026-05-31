# Changelog

All notable changes to **SmartIR(Mod)** since fork from [smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR).

Versions use `YYYYMMDDrN` format set in `manifest.json`.

---

## 20260531r0 — Tech debt sweep (2026-05-31)

### Fixed
- **fan.py** — `_async_power_sensor_changed` no longer sets `self._speed = None` when external power sensor turns on; restores last known on speed (or first speed in list) so downstream `percentage` / `state` / `send_command` paths remain valid. (D1)
- **controller.py** — Removed dead `command.replace("\\", "")` no-op in `MQTTController.send` and clarified payload behaviour. (D3)

### Changed
- **`__init__.py`** — Hub-level `smartir:` YAML now tolerates legacy `check_updates` / `update_branch` keys with a one-line deprecation warning, instead of failing config validation. (D2)
- **const.py** — Introduced `CODES_SOURCE_URL` template; 4 entity platform files now use it instead of inlined `f"…/main/codes/{platform}/{device_code}.json"`. (D4)
- **light.py** — Extracted brightness / colortemp step calculation into `_stepwise_command()` helper; removes duplication while preserving the historical edge-saturation behaviour. (D5)
- **config_flow.py** — `_make_unique_id` docstring now explicitly distinguishes the entity unique_id from the cross-HA `intent_id`. (D8)

### Docs
- **README.md** — Already updated previous day; no change.
- **docs/README.md** — Replaced upstream stub (with stale `check_updates` / `smartHomeHub` references) with a short index pointing to root README and per-platform docs. (D9)
- **docs/CLIMATE.md / MEDIA_PLAYER.md / FAN.md / LIGHT.md** — Added a "SmartIR(Mod) note" preamble pointing users to Config Flow UI as the primary setup path and to multi-HA sync; rest of upstream-derived schema reference kept intact. (D10)
- **CHANGELOG.md** — This file. (D11)

### CI
- **`.github/workflows/lint.yml`** — Added: ruff lint + Python compile check + JSON validity check (manifest / hacs / strings / translations) on every push and PR. (D14)

---

## 20260518r1 — MQTT race fix

### Fixed
- **intent_sync.py** — `_intent_subscribe` and `_intent_publish` now use `mqtt.async_wait_for_mqtt_client(hass)` instead of `mqtt_config_entry_enabled`, eliminating a startup race where SmartIR would try to subscribe before MQTT's `hass.data['mqtt']` was populated (`KeyError: 'mqtt'`).
- **manifest.json** — Added `"after_dependencies": ["mqtt"]` as a load-order hint.

---

## 20260518r0 — Cross-build intent_id consistency

### Added
- **`compute_intent_id(platform, device_code, controller_data)`** in `intent_sync.py` — derives a deterministic identifier from physical device identity (not entity name / unique_id).
- **`CONF_INTENT_ID`** persisted in `entry.data`. Backfilled on existing entries via `__init__.py async_setup_entry`.

### Fixed
- MQTT intent topic now identical across Config Flow and YAML-imported entries, so multi-HA fan-out works regardless of how each HA was set up. Previously: Config Flow used `{platform}_{device_code}_{name}` fallback (e.g., `climate_1090_BOOTS ROOM AC`), YAML used user-supplied `unique_id` (e.g., `boots_room_ac`) → different topics, no sync.

### Changed
- **intent_sync.py** — `_intent_setup` parameter renamed `unique_id` → `intent_id`; entity files updated.

---

## 20260517 — F6 Config Flow + multi-HA intent sync + audit sweep

### Added
- **Config Flow UI** (`config_flow.py`) — `SmartIRConfigFlow` (platform picker + per-platform forms) and `SmartIROptionsFlow` (`OptionsFlowWithReload`). YAML soft-imports via `SOURCE_IMPORT`; existing `unique_id` preserved so `entity_id` stays the same.
- **Multi-HA intent sync** — opt-in MQTT topic propagation of entity state across HA instances controlling the same physical device. New `intent_sync.py` with `SmartIRIntentMixin`; per-platform `_apply_intent` / `_build_intent_payload`. Backfills `CONF_INTENT_SOURCE_ID` (UUID per entry) on startup.
- **`const.py`** — central constants for `CONF_*`, `PLATFORMS`, `DEFAULT_DELAY`, etc.
- **`strings.json` + `translations/zh-Hant.json`** — UI labels for Config Flow / Options Flow.

### Fixed (7-item audit + 2 hotfixes)
- **F1** — `manifest.json` JSON syntax (missing comma) + required `integration_type: "hub"` and `iot_class: "local_push"` fields.
- **F2** — Migrated `hass.components.persistent_notification.async_create(...)` (removed in HA 2025.3) to `from homeassistant.components import persistent_notification; persistent_notification.async_create(hass, ...)` in `__init__.py` (5 sites). Subsequently the entire updater was removed (see below).
- **F3** — `Helper.downloader(session, source, dest)` 3-arg signature now matched at all 4 call sites; each platform fetches `session = async_get_clientsession(hass)` first.
- **F4** — Removed `@callback` decorator on `async def _async_*_changed` handlers (5 sites); `@callback` is for sync functions only.
- **F5** — `CONF_DELAY` schema unified to `cv.positive_float` across all 4 platforms (was `cv.string` in 3).
- **F7** — `hass.async_add_executor_job(os.makedirs, ..., exist_ok=True)` was passing `exist_ok` as a kwarg to `async_add_executor_job` (which only accepts positional args) — would `TypeError` at startup. Refactored to `functools.partial(os.makedirs, path, exist_ok=True)` across 5 sites.
- **bonus** — `aiohttp` timeout: bare int → `aiohttp.ClientTimeout(total=N)` (2 sites).
- **Hotfix: codes downloader URL** — Was pointing to upstream `smartHomeHub/SmartIR`, which has different `supportedController` values than this fork — using upstream codes broke MQTT-configured devices (wrong controller class). All 5 URLs now point to `raphael1688dev/SmartIR_Mod` on `main` branch.
- **Hotfix: swing_mode KeyError on state restore** — `async_added_to_hass` in climate.py trusted `last_state.attributes.get('swing_mode')` (returning `None`) and overwrote the valid `__init__` default. Now all 4 entity platforms validate restored values against legal sets / ranges and keep the `__init__` default on failure.
- **Hotfix: intent.py filename collision** — Initial multi-HA implementation lived in `intent.py`, which collided with HA's built-in `intent` (voice intent) platform discovery. Renamed to `intent_sync.py`; all 4 entity imports updated.

### Removed
- **Self-updater** — `manifest.json updater` block, `_update()` / `check_updates` / `update_component` services in `__init__.py`, and `services.yaml` (now empty so deleted). HACS handles updates.
- **`hass.components.*`** usage — fully replaced with direct imports per HA 2025.3+ requirement.

### Changed
- **manifest.json** — `name: "SmartIR(Mod)"`, `homeassistant: "2026.5.0"`, fork-pointing `documentation` / `issue_tracker` / `codeowners`. Versioning switched to `YYYYMMDDrN`.
- **Restore-state hardening** — All 4 entity platforms now validate `last_state.attributes` against legal value sets / type+range before restoring; invalid values keep `__init__` defaults.
- **hacs.json** — `homeassistant` minimum bumped to `2026.5.0` to match manifest.

---

## Pre-fork baseline

This fork was created from upstream `smartHomeHub/SmartIR` master at 2026-05-17. See upstream README for prior history.
