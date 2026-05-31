# SmartIR(Mod) Documentation

This directory contains per-platform setup references for **SmartIR(Mod)**, a fork of [smartHomeHub/SmartIR](https://github.com/smartHomeHub/SmartIR) modernised for Home Assistant 2026.5+.

## Start here

- **Installation, requirements, multi-HA sync** → [root README](../README.md)
- **Changelog** → [CHANGELOG.md](../CHANGELOG.md)

## Per-platform references

- [Climate](CLIMATE.md) — AC, heat pumps
- [Media Player](MEDIA_PLAYER.md) — TVs, receivers
- [Fan](FAN.md) — ceiling fans
- [Light](LIGHT.md) — IR-controlled lights

> The per-platform documents in this directory are derived from upstream SmartIR. They focus on **device JSON schema** (device code lists, command structure) and **YAML legacy configuration**. For new setups, prefer the Config Flow UI (Settings → Devices & Services → Add Integration → SmartIR(Mod)) — YAML is still accepted but auto-imports to a Config Entry on first run.
