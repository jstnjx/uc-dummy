# Unfolded Circle Dummy Integration

A self-contained virtual integration for exercising the Unfolded Circle Integration API without any physical device.
It is intended for testing an integration manager, setup implementation, Remote Two/Remote 3 integration UI, entity
subscription, command handling and live `entity_change` updates.

## What it tests

The driver starts with **no configured device** on a clean install so the full setup flow can be exercised.
Setup is multi-step:

1. Initial setup options from `driver.json`.
2. Select the single discovered virtual device (`Dummy Device 1`).
3. Configure its name, area and simulation interval.
4. Confirm the device.
5. Configuration is persisted under `UC_CONFIG_HOME` (or `$HOME`).
6. The complete entity set becomes available for selection/subscription.

Running setup again uses the same handler as a reconfiguration flow.

## Dummy device entities

| Entity | Type | Main controls / behavior |
|---|---|---|
| `dummy.button.action` | Button | Push increments the command counter |
| `dummy.switch.main` | Switch | On, off, toggle |
| `dummy.light.main` | Light | On/off/toggle, brightness, hue, saturation, color temperature |
| `dummy.cover.main` | Cover | Open, close, stop, set 0-100% position |
| `dummy.climate.main` | Climate | On/off, HVAC mode, target temperature; live current temperature |
| `dummy.media_player.main` | Media player | Power, play/pause, stop, next/previous, seek, volume, mute, repeat, shuffle, source, sound mode, DPAD, numpad, color/function buttons and simple commands |
| `dummy.ir_emitter.main` | IR emitter | PRONTO/HEX `send_ir`, two output ports, `stop_ir` |
| `dummy.remote.main` | Remote | Power, toggle, `send_cmd`, command sequences and simple commands |
| `dummy.select.mode` | Select | Direct/first/last/next/previous selection |
| `dummy.sensor.temperature` | Sensor | Simulated temperature |
| `dummy.sensor.humidity` | Sensor | Simulated humidity |
| `dummy.sensor.battery` | Sensor | Simulated battery |
| `dummy.sensor.command_counter` | Sensor | Counts every received entity command |

The four sensors can be disabled in setup. Every stateful command updates the Integration API entity attributes
immediately. Once the entity is subscribed, the `ucapi` configured-entity store therefore emits the corresponding
`entity_change` event back to the Remote / client.

## Run from source

Requirements: Python 3.11+ and a network path between the integration and the Remote/client.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
mkdir -p config
UC_CONFIG_HOME="$PWD/config" python src/driver.py
```

On Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
New-Item -ItemType Directory -Force config | Out-Null
$env:UC_CONFIG_HOME = "$PWD\config"
python .\src\driver.py
```

The default Integration API port is **9091**. Override it with `UC_INTEGRATION_HTTP_PORT` if required.
The `ucapi` library publishes `_uc-integration._tcp.local` via mDNS unless `UC_DISABLE_MDNS_PUBLISH=true` is set.

## Recommended Integration Manager test

1. Start this integration with an empty `config` directory.
2. Add it to the Integration Manager by its Integration API endpoint or mDNS discovery.
3. Read driver metadata and start `setup_driver`.
4. Verify the initial setup options are rendered.
5. Complete the three dynamic setup pages.
6. Refresh `get_available_entities` and confirm the entity IDs above are present.
7. Subscribe to several entities.
8. Send `entity_command` requests.
9. Verify both the command acknowledgement and subsequent `entity_change` event.
10. Start setup with `reconfigure=true`, change the name/area/interval, finish setup, and refresh available entities.

This sequence intentionally covers the difference between **available entities** and **configured/subscribed entities**.

## Build an installable Remote aarch64 package

The included build script follows the layout used by official Python integrations: root `driver.json`, `version.txt` and
`LICENSE`, plus `bin/driver` and the PyInstaller runtime.

Docker with ARM emulation/support:

```bash
./scripts/build-aarch64.sh
```

Output:

```text
uc-intg-dummy-v0.1.0-aarch64.tar.gz
uc-intg-dummy-v0.1.0-aarch64.sha256
```

There is also a GitHub Actions workflow. On an ARM64 GitHub runner it builds the same archive; a `v*` tag additionally
creates a GitHub release.

## Useful environment variables

| Variable | Purpose |
|---|---|
| `UC_CONFIG_HOME` | Persistent config directory |
| `UC_INTEGRATION_HTTP_PORT` | Override port 9091 |
| `UC_INTEGRATION_INTERFACE` | Bind/publish a specific interface |
| `UC_DISABLE_MDNS_PUBLISH` | Disable mDNS publication |
| `UC_DRIVER_PATH` | Explicit path to `driver.json` |
| `LOG_LEVEL` | Python log level, default `INFO` |

## Notes

- This is a virtual test integration: no outbound device/network connection is performed.
- Cover movement is instantaneous rather than time-based so command/event validation is deterministic.
- Media DPAD/numpad/menu/function commands return success and increment the command counter; state-bearing media
  commands additionally update entity attributes.
- The setup configuration file is `uc-dummy-integration.json` under `UC_CONFIG_HOME` or `$HOME`.
