"""Helpers shared by the test modules."""
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent

# The device codes the maintainer runs in production (MQTT, Zigbee2MQTT IR blasters).
PRODUCTION_CLIMATE_CODES = (1080, 1089, 1090)


def load_code(platform, code):
    return json.loads((REPO / "codes" / platform / f"{code}.json").read_text(encoding="utf-8"))


def all_codes(platform):
    return sorted(int(p.stem) for p in (REPO / "codes" / platform).glob("*.json"))


class FakeEntry:
    """The parts of a ConfigEntry the entities read."""

    def __init__(self, data, unique_id, options=None):
        self.data = data
        self.options = options or {}
        self.unique_id = unique_id
        self.entry_id = "test_entry"
        self.title = data.get("name", "test")


class RecordingController:
    def __init__(self):
        self.sent = []

    async def send(self, command):
        self.sent.append(command)


class FailingController:
    async def send(self, command):
        from homeassistant.exceptions import HomeAssistantError

        raise HomeAssistantError("send failed")


def capture_intents(entity):
    """Replace the MQTT intent publish with a list recorder."""
    published = []

    async def _publish(payload):
        published.append(dict(payload))

    entity._intent_publish = _publish
    return published


def ha_version():
    import homeassistant.const as const

    return const.MAJOR_VERSION, const.MINOR_VERSION
