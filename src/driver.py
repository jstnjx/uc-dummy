#!/usr/bin/env python3
"""Unfolded Circle dummy integration.

A deliberately feature-rich local integration for validating an Integration API
client / integration manager. It has no external device dependency: all state is
held in memory and, optionally, simulated.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import ucapi
import ucapi.button as uc_button
import ucapi.climate as uc_climate
import ucapi.cover as uc_cover
import ucapi.light as uc_light
import ucapi.ir_emitter as uc_ir
import ucapi.media_player as uc_media
import ucapi.remote as uc_remote
import ucapi.select as uc_select
import ucapi.sensor as uc_sensor
import ucapi.switch as uc_switch

_LOG = logging.getLogger("uc_dummy")

DEVICE_ID = "dummy-device-1"
CONFIG_FILE = "uc-dummy-integration.json"

BUTTON_ID = "dummy.button.action"
SWITCH_ID = "dummy.switch.main"
LIGHT_ID = "dummy.light.main"
COVER_ID = "dummy.cover.main"
CLIMATE_ID = "dummy.climate.main"
MEDIA_ID = "dummy.media_player.main"
IR_ID = "dummy.ir_emitter.main"
REMOTE_ID = "dummy.remote.main"
SELECT_ID = "dummy.select.mode"
TEMP_SENSOR_ID = "dummy.sensor.temperature"
HUMIDITY_SENSOR_ID = "dummy.sensor.humidity"
BATTERY_SENSOR_ID = "dummy.sensor.battery"
COUNTER_SENSOR_ID = "dummy.sensor.command_counter"

MEDIA_TRACKS = [
    {
        "title": "Dummy Track One",
        "artist": "Unfolded Circle",
        "album": "Integration Test",
        "duration": 210,
    },
    {
        "title": "Dummy Track Two",
        "artist": "Remote Three",
        "album": "Integration Test",
        "duration": 185,
    },
    {
        "title": "Dummy Track Three",
        "artist": "Integration API",
        "album": "Integration Test",
        "duration": 242,
    },
]

SELECT_OPTIONS = ["Normal", "Cinema", "Music", "Night"]
SOURCE_OPTIONS = ["HDMI 1", "HDMI 2", "Streaming", "Radio"]
SOUND_MODE_OPTIONS = ["Stereo", "Direct", "Movie", "Night"]
REMOTE_SIMPLE_COMMANDS = [
    "POWER",
    "INPUT_HDMI1",
    "INPUT_HDMI2",
    "FAVORITE_1",
    "FAVORITE_2",
]
MEDIA_SIMPLE_COMMANDS = ["DUMMY_ACTION", "FAVORITE_1"]


@dataclass(slots=True)
class DummyConfig:
    """Persistent integration configuration."""

    device_id: str = DEVICE_ID
    name: str = "UC Dummy Device"
    area: str = "Test Bench"
    simulate: bool = True
    include_sensors: bool = True
    update_interval: int = 5


loop = asyncio.new_event_loop()
api = ucapi.IntegrationAPI(loop)
config = DummyConfig()
_pending_setup: dict[str, Any] = {}
_simulation_task: asyncio.Task | None = None
_command_counter = 0
_media_index = 0


def _config_path() -> Path:
    base = Path(os.getenv("UC_CONFIG_HOME") or os.getenv("HOME") or ".")
    base.mkdir(parents=True, exist_ok=True)
    return base / CONFIG_FILE


def _metadata_path() -> Path:
    """Find driver.json in source runs and packaged Remote installs."""
    explicit = os.getenv("UC_DRIVER_PATH")
    if explicit:
        return Path(explicit)

    candidates = [
        Path.cwd() / "driver.json",
        Path(sys.executable).resolve().parent.parent / "driver.json",
        Path(__file__).resolve().parent.parent / "driver.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _clamp(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, parsed))


def _load_config() -> DummyConfig:
    path = _config_path()
    if not path.exists():
        return DummyConfig()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return DummyConfig(
            device_id=str(raw.get("device_id", DEVICE_ID)),
            name=str(raw.get("name", "UC Dummy Device")),
            area=str(raw.get("area", "Test Bench")),
            simulate=_as_bool(raw.get("simulate"), True),
            include_sensors=_as_bool(raw.get("include_sensors"), True),
            update_interval=_as_int(raw.get("update_interval"), 5, 1, 60),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        _LOG.exception("Could not read %s; using defaults", path)
        return DummyConfig()


def _save_config(value: DummyConfig) -> None:
    path = _config_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(asdict(value), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    _LOG.info("Saved dummy configuration to %s", path)


def _set_device(entity: ucapi.Entity) -> ucapi.Entity:
    entity.device_id = config.device_id
    return entity


def _build_entities() -> None:
    """Populate the available entity store from current configuration."""
    api.available_entities.clear()

    entities: list[ucapi.Entity] = [
        _set_device(
            ucapi.Button(
                BUTTON_ID,
                f"{config.name} Action",
                icon="uc:touch",
                description="Increments the dummy command counter.",
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(
            ucapi.Switch(
                SWITCH_ID,
                f"{config.name} Switch",
                features=[uc_switch.Features.ON_OFF, uc_switch.Features.TOGGLE],
                attributes={uc_switch.Attributes.STATE: uc_switch.States.OFF},
                device_class=uc_switch.DeviceClasses.SWITCH,
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(
            ucapi.Light(
                LIGHT_ID,
                f"{config.name} Light",
                features=[
                    uc_light.Features.ON_OFF,
                    uc_light.Features.TOGGLE,
                    uc_light.Features.DIM,
                    uc_light.Features.COLOR,
                    uc_light.Features.COLOR_TEMPERATURE,
                ],
                attributes={
                    uc_light.Attributes.STATE: uc_light.States.OFF,
                    uc_light.Attributes.BRIGHTNESS: 128,
                    uc_light.Attributes.HUE: 210,
                    uc_light.Attributes.SATURATION: 200,
                    uc_light.Attributes.COLOR_TEMPERATURE: 50,
                },
                options={uc_light.Options.COLOR_TEMPERATURE_STEPS: 100},
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(
            ucapi.Cover(
                COVER_ID,
                f"{config.name} Blind",
                features=[
                    uc_cover.Features.OPEN,
                    uc_cover.Features.CLOSE,
                    uc_cover.Features.STOP,
                    uc_cover.Features.POSITION,
                ],
                attributes={
                    uc_cover.Attributes.STATE: uc_cover.States.CLOSED,
                    uc_cover.Attributes.POSITION: 0,
                },
                device_class=uc_cover.DeviceClasses.BLIND,
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(
            ucapi.Climate(
                CLIMATE_ID,
                f"{config.name} Climate",
                features=[
                    uc_climate.Features.ON_OFF,
                    uc_climate.Features.HEAT,
                    uc_climate.Features.COOL,
                    uc_climate.Features.CURRENT_TEMPERATURE,
                    uc_climate.Features.TARGET_TEMPERATURE,
                ],
                attributes={
                    uc_climate.Attributes.STATE: uc_climate.States.OFF,
                    uc_climate.Attributes.CURRENT_TEMPERATURE: 21.0,
                    uc_climate.Attributes.TARGET_TEMPERATURE: 22.0,
                },
                options={
                    uc_climate.Options.TEMPERATURE_UNIT: "CELSIUS",
                    uc_climate.Options.TARGET_TEMPERATURE_STEP: 0.5,
                    uc_climate.Options.MIN_TEMPERATURE: 10,
                    uc_climate.Options.MAX_TEMPERATURE: 30,
                },
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(_make_media_player()),
        _set_device(
            ucapi.IREmitter(
                IR_ID,
                f"{config.name} IR Emitter",
                features=[uc_ir.Features.SEND_IR],
                attributes={uc_ir.Attributes.STATE: uc_ir.States.ON},
                options={
                    uc_ir.Options.PORTS: [
                        {"id": "port1", "name": "Dummy Port 1"},
                        {"id": "port2", "name": "Dummy Port 2"},
                    ],
                    uc_ir.Options.IR_FORMATS: ["HEX"],
                },
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(
            ucapi.Remote(
                REMOTE_ID,
                f"{config.name} Remote",
                features=[
                    uc_remote.Features.ON_OFF,
                    uc_remote.Features.TOGGLE,
                    uc_remote.Features.SEND_CMD,
                ],
                attributes={uc_remote.Attributes.STATE: uc_remote.States.OFF},
                simple_commands=REMOTE_SIMPLE_COMMANDS,
                icon="uc:remote",
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
        _set_device(
            ucapi.Select(
                SELECT_ID,
                f"{config.name} Mode",
                attributes={
                    uc_select.Attributes.STATE: uc_select.States.ON,
                    uc_select.Attributes.CURRENT_OPTION: SELECT_OPTIONS[0],
                    uc_select.Attributes.OPTIONS: SELECT_OPTIONS,
                },
                icon="uc:list",
                area=config.area,
                cmd_handler=entity_command,
            )
        ),
    ]

    if config.include_sensors:
        entities.extend(
            [
                _set_device(
                    ucapi.Sensor(
                        TEMP_SENSOR_ID,
                        f"{config.name} Temperature",
                        features=[],
                        attributes={
                            uc_sensor.Attributes.STATE: uc_sensor.States.ON,
                            uc_sensor.Attributes.VALUE: 21.0,
                        },
                        device_class=uc_sensor.DeviceClasses.TEMPERATURE,
                        options={
                            uc_sensor.Options.NATIVE_UNIT: "CELSIUS",
                            uc_sensor.Options.DECIMALS: 1,
                        },
                        area=config.area,
                    )
                ),
                _set_device(
                    ucapi.Sensor(
                        HUMIDITY_SENSOR_ID,
                        f"{config.name} Humidity",
                        features=[],
                        attributes={
                            uc_sensor.Attributes.STATE: uc_sensor.States.ON,
                            uc_sensor.Attributes.VALUE: 45.0,
                        },
                        device_class=uc_sensor.DeviceClasses.HUMIDITY,
                        options={uc_sensor.Options.DECIMALS: 1},
                        area=config.area,
                    )
                ),
                _set_device(
                    ucapi.Sensor(
                        BATTERY_SENSOR_ID,
                        f"{config.name} Battery",
                        features=[],
                        attributes={
                            uc_sensor.Attributes.STATE: uc_sensor.States.ON,
                            uc_sensor.Attributes.VALUE: 100,
                        },
                        device_class=uc_sensor.DeviceClasses.BATTERY,
                        options={uc_sensor.Options.DECIMALS: 0},
                        area=config.area,
                    )
                ),
                _set_device(
                    ucapi.Sensor(
                        COUNTER_SENSOR_ID,
                        f"{config.name} Command Counter",
                        features=[],
                        attributes={
                            uc_sensor.Attributes.STATE: uc_sensor.States.ON,
                            uc_sensor.Attributes.VALUE: _command_counter,
                            uc_sensor.Attributes.UNIT: "commands",
                        },
                        device_class=uc_sensor.DeviceClasses.CUSTOM,
                        options={
                            uc_sensor.Options.CUSTOM_UNIT: "commands",
                            uc_sensor.Options.DECIMALS: 0,
                        },
                        area=config.area,
                    )
                ),
            ]
        )

    for entity in entities:
        api.available_entities.add(entity)

    _LOG.info("Published %d dummy entities", len(entities))


def _make_media_player() -> ucapi.MediaPlayer:
    track = MEDIA_TRACKS[_media_index]
    return ucapi.MediaPlayer(
        MEDIA_ID,
        f"{config.name} Media Player",
        features=[
            uc_media.Features.ON_OFF,
            uc_media.Features.TOGGLE,
            uc_media.Features.VOLUME,
            uc_media.Features.VOLUME_UP_DOWN,
            uc_media.Features.MUTE_TOGGLE,
            uc_media.Features.MUTE,
            uc_media.Features.UNMUTE,
            uc_media.Features.PLAY_PAUSE,
            uc_media.Features.STOP,
            uc_media.Features.NEXT,
            uc_media.Features.PREVIOUS,
            uc_media.Features.SEEK,
            uc_media.Features.REPEAT,
            uc_media.Features.SHUFFLE,
            uc_media.Features.MEDIA_DURATION,
            uc_media.Features.MEDIA_POSITION,
            uc_media.Features.MEDIA_TITLE,
            uc_media.Features.MEDIA_ARTIST,
            uc_media.Features.MEDIA_ALBUM,
            uc_media.Features.MEDIA_TYPE,
            uc_media.Features.DPAD,
            uc_media.Features.NUMPAD,
            uc_media.Features.HOME,
            uc_media.Features.MENU,
            uc_media.Features.INFO,
            uc_media.Features.COLOR_BUTTONS,
            uc_media.Features.CHANNEL_SWITCHER,
            uc_media.Features.SELECT_SOURCE,
            uc_media.Features.SELECT_SOUND_MODE,
            uc_media.Features.SETTINGS,
        ],
        attributes={
            uc_media.Attributes.STATE: uc_media.States.OFF,
            uc_media.Attributes.VOLUME: 35,
            uc_media.Attributes.MUTED: False,
            uc_media.Attributes.MEDIA_DURATION: track["duration"],
            uc_media.Attributes.MEDIA_POSITION: 0,
            uc_media.Attributes.MEDIA_TYPE: uc_media.MediaContentType.TRACK,
            uc_media.Attributes.MEDIA_TITLE: track["title"],
            uc_media.Attributes.MEDIA_ARTIST: track["artist"],
            uc_media.Attributes.MEDIA_ALBUM: track["album"],
            uc_media.Attributes.REPEAT: uc_media.RepeatMode.OFF,
            uc_media.Attributes.SHUFFLE: False,
            uc_media.Attributes.SOURCE: SOURCE_OPTIONS[0],
            uc_media.Attributes.SOURCE_LIST: SOURCE_OPTIONS,
            uc_media.Attributes.SOUND_MODE: SOUND_MODE_OPTIONS[0],
            uc_media.Attributes.SOUND_MODE_LIST: SOUND_MODE_OPTIONS,
        },
        device_class=uc_media.DeviceClasses.TV,
        options={
            uc_media.Options.SIMPLE_COMMANDS: MEDIA_SIMPLE_COMMANDS,
            uc_media.Options.VOLUME_STEPS: 100,
        },
        area=config.area,
        cmd_handler=entity_command,
    )


def _update(entity_id: str, attributes: dict[Any, Any]) -> bool:
    """Update configured state (and thereby broadcast entity_change)."""
    if api.configured_entities.contains(entity_id):
        return api.configured_entities.update_attributes(entity_id, attributes)

    # Keep the advertised instance current even if it is not subscribed yet.
    entity = api.available_entities.get(entity_id)
    if entity is None:
        return False
    entity.attributes.update(attributes)
    return True


def _current(entity: ucapi.Entity, attribute: Any, default: Any = None) -> Any:
    return entity.attributes.get(attribute, default)


def _bump_counter() -> None:
    global _command_counter
    _command_counter += 1
    if config.include_sensors:
        _update(COUNTER_SENSOR_ID, {uc_sensor.Attributes.VALUE: _command_counter})


def _bad(message: str) -> ucapi.StatusCodes:
    _LOG.warning(message)
    return ucapi.StatusCodes.BAD_REQUEST


async def entity_command(
    entity: ucapi.Entity,
    cmd_id: str,
    params: dict[str, Any] | None,
    websocket: Any = None,
) -> ucapi.StatusCodes:
    """Dispatch every controllable dummy entity command."""
    del websocket
    params = params or {}
    _LOG.info("Command %s -> %s %s", entity.id, cmd_id, params)
    _bump_counter()

    if entity.id == BUTTON_ID:
        return ucapi.StatusCodes.OK if cmd_id == uc_button.Commands.PUSH else ucapi.StatusCodes.NOT_IMPLEMENTED
    if entity.id == SWITCH_ID:
        return _switch_command(entity, cmd_id)
    if entity.id == LIGHT_ID:
        return _light_command(entity, cmd_id, params)
    if entity.id == COVER_ID:
        return _cover_command(entity, cmd_id, params)
    if entity.id == CLIMATE_ID:
        return _climate_command(entity, cmd_id, params)
    if entity.id == MEDIA_ID:
        return _media_command(entity, cmd_id, params)
    if entity.id == IR_ID:
        return _ir_command(entity, cmd_id, params)
    if entity.id == REMOTE_ID:
        return _remote_command(entity, cmd_id, params)
    if entity.id == SELECT_ID:
        return _select_command(entity, cmd_id, params)

    return ucapi.StatusCodes.NOT_IMPLEMENTED


def _switch_command(entity: ucapi.Entity, cmd_id: str) -> ucapi.StatusCodes:
    if cmd_id == uc_switch.Commands.ON:
        state = uc_switch.States.ON
    elif cmd_id == uc_switch.Commands.OFF:
        state = uc_switch.States.OFF
    elif cmd_id == uc_switch.Commands.TOGGLE:
        state = (
            uc_switch.States.OFF
            if _current(entity, uc_switch.Attributes.STATE) == uc_switch.States.ON
            else uc_switch.States.ON
        )
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED
    _update(entity.id, {uc_switch.Attributes.STATE: state})
    return ucapi.StatusCodes.OK


def _light_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    changes: dict[Any, Any] = {}
    if cmd_id == uc_light.Commands.ON:
        changes[uc_light.Attributes.STATE] = uc_light.States.ON
    elif cmd_id == uc_light.Commands.OFF:
        changes[uc_light.Attributes.STATE] = uc_light.States.OFF
    elif cmd_id == uc_light.Commands.TOGGLE:
        changes[uc_light.Attributes.STATE] = (
            uc_light.States.OFF
            if _current(entity, uc_light.Attributes.STATE) == uc_light.States.ON
            else uc_light.States.ON
        )
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    if "brightness" in params:
        changes[uc_light.Attributes.BRIGHTNESS] = int(_clamp(params["brightness"], 0, 255))
    if "hue" in params:
        changes[uc_light.Attributes.HUE] = int(_clamp(params["hue"], 0, 360))
    if "saturation" in params:
        changes[uc_light.Attributes.SATURATION] = int(_clamp(params["saturation"], 0, 255))
    if "color_temperature" in params:
        changes[uc_light.Attributes.COLOR_TEMPERATURE] = int(
            _clamp(params["color_temperature"], 0, 100)
        )

    _update(entity.id, changes)
    return ucapi.StatusCodes.OK


def _cover_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    if cmd_id == uc_cover.Commands.OPEN:
        changes = {
            uc_cover.Attributes.STATE: uc_cover.States.OPEN,
            uc_cover.Attributes.POSITION: 100,
        }
    elif cmd_id == uc_cover.Commands.CLOSE:
        changes = {
            uc_cover.Attributes.STATE: uc_cover.States.CLOSED,
            uc_cover.Attributes.POSITION: 0,
        }
    elif cmd_id == uc_cover.Commands.POSITION:
        if "position" not in params:
            return _bad("Cover position command requires position")
        position = int(_clamp(params["position"], 0, 100))
        changes = {
            uc_cover.Attributes.POSITION: position,
            uc_cover.Attributes.STATE: (
                uc_cover.States.CLOSED if position == 0 else uc_cover.States.OPEN
            ),
        }
    elif cmd_id == uc_cover.Commands.STOP:
        position = int(_current(entity, uc_cover.Attributes.POSITION, 0))
        changes = {
            uc_cover.Attributes.STATE: (
                uc_cover.States.CLOSED if position == 0 else uc_cover.States.OPEN
            )
        }
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    _update(entity.id, changes)
    return ucapi.StatusCodes.OK


def _climate_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    changes: dict[Any, Any] = {}
    valid_modes = {
        uc_climate.States.OFF,
        uc_climate.States.HEAT,
        uc_climate.States.COOL,
        uc_climate.States.HEAT_COOL,
        uc_climate.States.FAN,
        uc_climate.States.AUTO,
    }

    if cmd_id == uc_climate.Commands.ON:
        old = _current(entity, uc_climate.Attributes.STATE)
        changes[uc_climate.Attributes.STATE] = (
            uc_climate.States.HEAT if old == uc_climate.States.OFF else old
        )
    elif cmd_id == uc_climate.Commands.OFF:
        changes[uc_climate.Attributes.STATE] = uc_climate.States.OFF
    elif cmd_id == uc_climate.Commands.HVAC_MODE:
        raw_mode = str(params.get("hvac_mode", "")).upper()
        try:
            mode = uc_climate.States(raw_mode)
        except ValueError:
            return _bad(f"Invalid climate mode: {raw_mode}")
        if mode not in valid_modes:
            return _bad(f"Unsupported climate mode: {raw_mode}")
        changes[uc_climate.Attributes.STATE] = mode
        if "temperature" in params:
            changes[uc_climate.Attributes.TARGET_TEMPERATURE] = round(
                _clamp(params["temperature"], 10, 30) * 2
            ) / 2
    elif cmd_id == uc_climate.Commands.TARGET_TEMPERATURE:
        if "temperature" not in params:
            return _bad("Target temperature command requires temperature")
        changes[uc_climate.Attributes.TARGET_TEMPERATURE] = round(
            _clamp(params["temperature"], 10, 30) * 2
        ) / 2
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    _update(entity.id, changes)
    return ucapi.StatusCodes.OK


def _media_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    global _media_index
    changes: dict[Any, Any] = {}
    state = _current(entity, uc_media.Attributes.STATE, uc_media.States.OFF)

    if cmd_id == uc_media.Commands.ON:
        changes[uc_media.Attributes.STATE] = uc_media.States.ON
    elif cmd_id == uc_media.Commands.OFF:
        changes[uc_media.Attributes.STATE] = uc_media.States.OFF
    elif cmd_id == uc_media.Commands.TOGGLE:
        changes[uc_media.Attributes.STATE] = (
            uc_media.States.OFF if state != uc_media.States.OFF else uc_media.States.ON
        )
    elif cmd_id == uc_media.Commands.PLAY_PAUSE:
        changes[uc_media.Attributes.STATE] = (
            uc_media.States.PAUSED
            if state == uc_media.States.PLAYING
            else uc_media.States.PLAYING
        )
    elif cmd_id == uc_media.Commands.STOP:
        changes.update(
            {
                uc_media.Attributes.STATE: uc_media.States.ON,
                uc_media.Attributes.MEDIA_POSITION: 0,
            }
        )
    elif cmd_id in {uc_media.Commands.NEXT, uc_media.Commands.PREVIOUS}:
        direction = 1 if cmd_id == uc_media.Commands.NEXT else -1
        _media_index = (_media_index + direction) % len(MEDIA_TRACKS)
        track = MEDIA_TRACKS[_media_index]
        changes.update(
            {
                uc_media.Attributes.MEDIA_TITLE: track["title"],
                uc_media.Attributes.MEDIA_ARTIST: track["artist"],
                uc_media.Attributes.MEDIA_ALBUM: track["album"],
                uc_media.Attributes.MEDIA_DURATION: track["duration"],
                uc_media.Attributes.MEDIA_POSITION: 0,
                uc_media.Attributes.STATE: uc_media.States.PLAYING,
            }
        )
    elif cmd_id == uc_media.Commands.SEEK:
        if "media_position" not in params:
            return _bad("Seek command requires media_position")
        duration = float(_current(entity, uc_media.Attributes.MEDIA_DURATION, 0))
        changes[uc_media.Attributes.MEDIA_POSITION] = int(
            _clamp(params["media_position"], 0, duration)
        )
    elif cmd_id == uc_media.Commands.VOLUME:
        if "volume" not in params:
            return _bad("Volume command requires volume")
        changes[uc_media.Attributes.VOLUME] = int(_clamp(params["volume"], 0, 100))
    elif cmd_id in {uc_media.Commands.VOLUME_UP, uc_media.Commands.VOLUME_DOWN}:
        volume = int(_current(entity, uc_media.Attributes.VOLUME, 0))
        volume += 5 if cmd_id == uc_media.Commands.VOLUME_UP else -5
        changes[uc_media.Attributes.VOLUME] = int(_clamp(volume, 0, 100))
    elif cmd_id == uc_media.Commands.MUTE_TOGGLE:
        changes[uc_media.Attributes.MUTED] = not bool(
            _current(entity, uc_media.Attributes.MUTED, False)
        )
    elif cmd_id == uc_media.Commands.MUTE:
        changes[uc_media.Attributes.MUTED] = True
    elif cmd_id == uc_media.Commands.UNMUTE:
        changes[uc_media.Attributes.MUTED] = False
    elif cmd_id == uc_media.Commands.REPEAT:
        raw = str(params.get("repeat", "")).upper()
        try:
            changes[uc_media.Attributes.REPEAT] = uc_media.RepeatMode(raw)
        except ValueError:
            return _bad(f"Invalid repeat mode: {raw}")
    elif cmd_id == uc_media.Commands.SHUFFLE:
        if "shuffle" not in params:
            return _bad("Shuffle command requires shuffle")
        changes[uc_media.Attributes.SHUFFLE] = _as_bool(params["shuffle"])
    elif cmd_id == uc_media.Commands.SELECT_SOURCE:
        source = str(params.get("source", ""))
        if source not in SOURCE_OPTIONS:
            return _bad(f"Unknown media source: {source}")
        changes[uc_media.Attributes.SOURCE] = source
    elif cmd_id == uc_media.Commands.SELECT_SOUND_MODE:
        sound_mode = str(params.get("sound_mode", ""))
        if sound_mode not in SOUND_MODE_OPTIONS:
            return _bad(f"Unknown sound mode: {sound_mode}")
        changes[uc_media.Attributes.SOUND_MODE] = sound_mode
    elif cmd_id in MEDIA_SIMPLE_COMMANDS:
        return ucapi.StatusCodes.OK
    elif cmd_id in {
        uc_media.Commands.CURSOR_UP,
        uc_media.Commands.CURSOR_DOWN,
        uc_media.Commands.CURSOR_LEFT,
        uc_media.Commands.CURSOR_RIGHT,
        uc_media.Commands.CURSOR_ENTER,
        uc_media.Commands.DIGIT_0,
        uc_media.Commands.DIGIT_1,
        uc_media.Commands.DIGIT_2,
        uc_media.Commands.DIGIT_3,
        uc_media.Commands.DIGIT_4,
        uc_media.Commands.DIGIT_5,
        uc_media.Commands.DIGIT_6,
        uc_media.Commands.DIGIT_7,
        uc_media.Commands.DIGIT_8,
        uc_media.Commands.DIGIT_9,
        uc_media.Commands.HOME,
        uc_media.Commands.MENU,
        uc_media.Commands.INFO,
        uc_media.Commands.BACK,
        uc_media.Commands.FUNCTION_RED,
        uc_media.Commands.FUNCTION_GREEN,
        uc_media.Commands.FUNCTION_YELLOW,
        uc_media.Commands.FUNCTION_BLUE,
        uc_media.Commands.CHANNEL_UP,
        uc_media.Commands.CHANNEL_DOWN,
        uc_media.Commands.SETTINGS,
    }:
        return ucapi.StatusCodes.OK
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    _update(entity.id, changes)
    return ucapi.StatusCodes.OK


def _ir_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    if cmd_id == uc_ir.Commands.SEND_IR:
        code = str(params.get("code", "")).strip()
        if not code:
            return _bad("send_ir requires code")
        port = str(params.get("port", "port1"))
        if port not in {"port1", "port2"}:
            return _bad(f"Unknown IR port: {port}")
        repeat = _as_int(params.get("repeat"), 1, 1, 100)
        ir_format = str(params.get("format", "PRONTO")).upper()
        if ir_format not in {"PRONTO", "HEX"}:
            return _bad(f"Unsupported IR format: {ir_format}")
        _LOG.info(
            "Dummy IR send: port=%s format=%s repeat=%s code=%s",
            port,
            ir_format,
            repeat,
            code[:80],
        )
        return ucapi.StatusCodes.OK
    if cmd_id == uc_ir.Commands.STOP_IR:
        port = str(params.get("port", "port1"))
        if port not in {"port1", "port2"}:
            return _bad(f"Unknown IR port: {port}")
        _LOG.info("Dummy IR stop: port=%s", port)
        return ucapi.StatusCodes.OK
    return ucapi.StatusCodes.NOT_IMPLEMENTED


def _remote_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    if cmd_id == uc_remote.Commands.ON:
        _update(entity.id, {uc_remote.Attributes.STATE: uc_remote.States.ON})
    elif cmd_id == uc_remote.Commands.OFF:
        _update(entity.id, {uc_remote.Attributes.STATE: uc_remote.States.OFF})
    elif cmd_id == uc_remote.Commands.TOGGLE:
        new_state = (
            uc_remote.States.OFF
            if _current(entity, uc_remote.Attributes.STATE) == uc_remote.States.ON
            else uc_remote.States.ON
        )
        _update(entity.id, {uc_remote.Attributes.STATE: new_state})
    elif cmd_id == uc_remote.Commands.SEND_CMD:
        command = params.get("command")
        if not command:
            return _bad("send_cmd requires command")
        _LOG.info("Dummy remote command: %s", command)
    elif cmd_id == uc_remote.Commands.SEND_CMD_SEQUENCE:
        sequence = params.get("sequence")
        if not isinstance(sequence, list) or not sequence:
            return _bad("send_cmd_sequence requires a non-empty sequence")
        _LOG.info("Dummy remote sequence: %s", sequence)
    elif cmd_id in REMOTE_SIMPLE_COMMANDS:
        _LOG.info("Dummy remote simple command: %s", cmd_id)
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED
    return ucapi.StatusCodes.OK


def _select_command(
    entity: ucapi.Entity, cmd_id: str, params: dict[str, Any]
) -> ucapi.StatusCodes:
    options = list(_current(entity, uc_select.Attributes.OPTIONS, SELECT_OPTIONS))
    current = _current(entity, uc_select.Attributes.CURRENT_OPTION, options[0])
    try:
        index = options.index(current)
    except ValueError:
        index = 0

    if cmd_id == uc_select.Commands.SELECT_OPTION:
        option = str(params.get("option", ""))
        if option not in options:
            return _bad(f"Unknown select option: {option}")
        new_option = option
    elif cmd_id == uc_select.Commands.SELECT_FIRST:
        new_option = options[0]
    elif cmd_id == uc_select.Commands.SELECT_LAST:
        new_option = options[-1]
    elif cmd_id in {uc_select.Commands.SELECT_NEXT, uc_select.Commands.SELECT_PREVIOUS}:
        step = 1 if cmd_id == uc_select.Commands.SELECT_NEXT else -1
        next_index = index + step
        cycle = _as_bool(params.get("cycle"), True)
        if cycle:
            next_index %= len(options)
        else:
            next_index = max(0, min(len(options) - 1, next_index))
        new_option = options[next_index]
    else:
        return ucapi.StatusCodes.NOT_IMPLEMENTED

    _update(entity.id, {uc_select.Attributes.CURRENT_OPTION: new_option})
    return ucapi.StatusCodes.OK


async def _simulation_loop() -> None:
    """Generate deterministic live updates for subscribed dummy entities."""
    while True:
        await asyncio.sleep(max(1, config.update_interval))
        if not config.simulate:
            continue

        now = time.monotonic()
        temperature = round(21.0 + math.sin(now / 20.0) * 1.5, 1)
        humidity = round(45.0 + math.sin(now / 27.0) * 7.5, 1)
        battery = max(1, 100 - int(now / 300) % 100)

        if config.include_sensors:
            _update(TEMP_SENSOR_ID, {uc_sensor.Attributes.VALUE: temperature})
            _update(HUMIDITY_SENSOR_ID, {uc_sensor.Attributes.VALUE: humidity})
            _update(BATTERY_SENSOR_ID, {uc_sensor.Attributes.VALUE: battery})

        _update(
            CLIMATE_ID,
            {uc_climate.Attributes.CURRENT_TEMPERATURE: temperature},
        )

        media = api.available_entities.get(MEDIA_ID)
        if media and _current(media, uc_media.Attributes.STATE) == uc_media.States.PLAYING:
            position = int(_current(media, uc_media.Attributes.MEDIA_POSITION, 0))
            duration = int(_current(media, uc_media.Attributes.MEDIA_DURATION, 0))
            position += max(1, config.update_interval)
            if duration > 0 and position >= duration:
                position = 0
            _update(MEDIA_ID, {uc_media.Attributes.MEDIA_POSITION: position})


def _restart_simulation() -> None:
    global _simulation_task
    if _simulation_task is not None and not _simulation_task.done():
        _simulation_task.cancel()
    _simulation_task = loop.create_task(_simulation_loop())


def _device_selection_page() -> ucapi.RequestUserInput:
    return ucapi.RequestUserInput(
        title={"en": "Select dummy device"},
        settings=[
            {
                "label": {
                    "en": "This integration intentionally exposes one virtual device. No network scan is required."
                },
                "field": {"label": {"value": "UC Dummy Device"}},
            },
            {
                "id": "device.id",
                "label": {"en": "Device"},
                "field": {
                    "dropdown": {
                        "value": config.device_id,
                        "items": [
                            {
                                "id": DEVICE_ID,
                                "label": {"en": "Dummy Device 1"},
                            }
                        ],
                    }
                },
            },
        ],
    )


def _device_options_page() -> ucapi.RequestUserInput:
    name = str(_pending_setup.get("name", config.name))
    area = str(_pending_setup.get("area", config.area))
    interval = int(_pending_setup.get("update_interval", config.update_interval))
    return ucapi.RequestUserInput(
        title={"en": "Configure dummy device"},
        settings=[
            {
                "id": "device.name",
                "label": {"en": "Device name"},
                "field": {"text": {"value": name}},
            },
            {
                "id": "device.area",
                "label": {"en": "Area"},
                "field": {
                    "dropdown": {
                        "value": area,
                        "items": [
                            {"id": "Test Bench", "label": {"en": "Test Bench"}},
                            {"id": "Living Room", "label": {"en": "Living Room"}},
                            {"id": "Office", "label": {"en": "Office"}},
                            {"id": "Lab", "label": {"en": "Lab"}},
                        ],
                    }
                },
            },
            {
                "id": "device.interval",
                "label": {"en": "Simulation update interval (seconds)"},
                "field": {
                    "number": {
                        "value": interval,
                        "min": 1,
                        "max": 60,
                        "steps": 1,
                        "unit": "s",
                    }
                },
            },
        ],
    )


async def setup_handler(msg: ucapi.SetupDriver) -> ucapi.SetupAction:
    """Complete multi-step setup and reconfiguration flow."""
    global config, _pending_setup

    if isinstance(msg, ucapi.DriverSetupRequest):
        _pending_setup = {
            "reconfigure": msg.reconfigure,
            "simulate": _as_bool(msg.setup_data.get("simulate"), config.simulate),
            "include_sensors": _as_bool(
                msg.setup_data.get("include_sensors"), config.include_sensors
            ),
            "device_id": config.device_id,
            "name": config.name,
            "area": config.area,
            "update_interval": config.update_interval,
        }
        return _device_selection_page()

    if isinstance(msg, ucapi.UserDataResponse):
        values = msg.input_values

        # Values from previous screens may be included, so process the final page first.
        if "device.name" in values or "device.interval" in values:
            _pending_setup["device_id"] = str(values.get("device.id", DEVICE_ID))
            _pending_setup["name"] = str(values.get("device.name", config.name)).strip() or config.name
            _pending_setup["area"] = str(values.get("device.area", config.area))
            _pending_setup["update_interval"] = _as_int(
                values.get("device.interval"), config.update_interval, 1, 60
            )
            return ucapi.RequestUserConfirmation(
                title={"en": "Confirm dummy device"},
                header={
                    "en": f"Add {_pending_setup['name']} in {_pending_setup['area']}?"
                },
                footer={
                    "en": "All controls are virtual. Commands immediately update the matching entity state."
                },
            )

        if "device.id" in values:
            device_id = str(values.get("device.id", ""))
            if device_id != DEVICE_ID:
                return ucapi.SetupError(ucapi.IntegrationSetupError.NOT_FOUND)
            _pending_setup["device_id"] = device_id
            return _device_options_page()

        return ucapi.SetupError(ucapi.IntegrationSetupError.OTHER)

    if isinstance(msg, ucapi.UserConfirmationResponse):
        if not msg.confirm:
            _pending_setup = {}
            return ucapi.SetupError(ucapi.IntegrationSetupError.OTHER)

        config = DummyConfig(
            device_id=str(_pending_setup.get("device_id", DEVICE_ID)),
            name=str(_pending_setup.get("name", "UC Dummy Device")),
            area=str(_pending_setup.get("area", "Test Bench")),
            simulate=_as_bool(_pending_setup.get("simulate"), True),
            include_sensors=_as_bool(_pending_setup.get("include_sensors"), True),
            update_interval=_as_int(
                _pending_setup.get("update_interval"), 5, 1, 60
            ),
        )
        _save_config(config)

        # Keep configured IDs valid during reconfiguration, but refresh advertised metadata.
        api.configured_entities.clear()
        _build_entities()
        _restart_simulation()
        _pending_setup = {}
        return ucapi.SetupComplete()

    if isinstance(msg, ucapi.AbortDriverSetup):
        _LOG.info("Setup aborted: %s", msg.error)
        _pending_setup = {}
        return ucapi.SetupError(msg.error)

    return ucapi.SetupError(ucapi.IntegrationSetupError.OTHER)


@api.listens_to(ucapi.Events.CONNECT)
async def on_connect(**_: Any) -> None:
    """The virtual device is always reachable once the integration is running."""
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_disconnect(**_: Any) -> None:
    _LOG.debug("Remote disconnected")


@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def on_standby(**_: Any) -> None:
    _LOG.debug("Remote entered standby")


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def on_wakeup(**_: Any) -> None:
    _LOG.debug("Remote exited standby")


def main() -> None:
    global config
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Only advertise entities before setup when a persisted configuration exists.
    path = _config_path()
    if path.exists():
        config = _load_config()
        _build_entities()
        _restart_simulation()
    else:
        _LOG.info("No configuration yet. Run the integration setup flow.")

    metadata = _metadata_path()
    _LOG.info("Using driver metadata: %s", metadata)
    loop.run_until_complete(api.init(str(metadata), setup_handler))

    if _simulation_task is None:
        _restart_simulation()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        _LOG.info("Stopping dummy integration")
    finally:
        if _simulation_task is not None:
            _simulation_task.cancel()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


if __name__ == "__main__":
    main()
