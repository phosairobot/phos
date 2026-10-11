"""Write-only Web-facing facade for encrypted PHOS secrets."""
from robot.secrets import SecretsService

SLOTS = (
    ("tts.elevenlabs.api_key", "ElevenLabs API Key"),
    ("tts.google.credentials", "Google TTS Credentials"),
    ("tts.cartesia.api_key", "Cartesia API Key"),
)


class WebSecretsService:
    """Intentionally omits the runtime-only ``get_secret`` method."""
    def __init__(self, service: SecretsService) -> None:
        self._service = service

    def slots(self):
        return tuple({"name": name, "label": label, "configured": self._service.has_secret(name)}
                     for name, label in SLOTS)

    def set_secret(self, name: str, value: str) -> None:
        if name not in dict(SLOTS):
            raise ValueError("Unsupported credential slot.")
        self._service.set_secret(name, value)

    def remove_secret(self, name: str) -> bool:
        if name not in dict(SLOTS):
            raise ValueError("Unsupported credential slot.")
        return self._service.remove_secret(name)
