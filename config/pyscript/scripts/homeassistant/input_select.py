"""PyScript functions/services specifically for input selects"""
from socket import gethostname
from typing import Any, Callable

if gethostname() != "homeassistant":
    from helpers import local_setup

    log, _, sync_mock, decorator, __ = local_setup()
    service: Callable[..., Callable[..., Any]] = decorator
    state = sync_mock
    input_select = sync_mock


@service
def init_entity_force_updater_options() -> None:
    """Update the options for the force updater input select"""

    entity_ids = []
    for domain in [
        "binary_sensor",
        "climate",
        "cover",
        "device_tracker",
        "light",
        "media_player",
        "person",
        "remote",
        "scene",
        "script",
        "sensor",
        "sun",
        "switch",
        "var",
        "water_heater",
        "weather",
    ]:
        entity_ids.extend(state.names(domain=domain))

    input_select.set_options(
        entity_id="input_select.entity_force_updater", options=entity_ids
    )
