from .base import HomeAssistantBackend
from .client import (
    HomeAssistantAuthError,
    HomeAssistantConnectionError,
    HomeAssistantError,
    WebSocketHomeAssistant,
)
from .entities import SUPPORTED_DOMAINS, EntityInfo, filter_by_domains, split_domain
from .mock import MockHomeAssistant
from .state import StateCache
from .tools import build_ha_registry

__all__ = [
    "HomeAssistantBackend",
    "HomeAssistantError",
    "HomeAssistantAuthError",
    "HomeAssistantConnectionError",
    "WebSocketHomeAssistant",
    "MockHomeAssistant",
    "StateCache",
    "EntityInfo",
    "SUPPORTED_DOMAINS",
    "filter_by_domains",
    "split_domain",
    "build_ha_registry",
    "create_backend",
]


def create_backend(
    state_cache: StateCache | None = None,
    on_state_changed=None,
    logger=print,
) -> HomeAssistantBackend:
    """Mock oder echter WebSocket-Client, je nach Konfiguration.

    Faellt bei fehlender URL oder Verbindungsfehler auf den Mock zurueck,
    damit die Pipeline weiterlaeuft (Plan §20).
    """
    import config

    if config.HA_USE_MOCK or not config.HA_URL:
        backend = MockHomeAssistant()
        backend.connect()
        logger("[HA] Mock-Backend aktiv")
        return backend

    backend = WebSocketHomeAssistant(
        config.HA_URL,
        config.HA_TOKEN,
        reconnect=config.HA_RECONNECT,
        reconnect_delay=config.HA_RECONNECT_DELAY,
        state_cache=state_cache,
        on_state_changed=on_state_changed,
        logger=logger,
    )
    try:
        backend.connect()
    except HomeAssistantError as exc:
        logger(f"[HA ERROR] {exc} – wechsle auf Mock")
        fallback = MockHomeAssistant()
        fallback.connect()
        return fallback
    return backend
