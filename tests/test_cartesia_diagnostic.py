import io
from pathlib import Path
import wave

from scripts import test_cartesia_tts as diagnostic
from robot.voice import CartesiaTTSProvider


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        output.writeframes(b"\x01\0\x02\0")
    return buffer.getvalue()


def test_cli_defaults_and_sdk_request_are_safe(tmp_path, capsys):
    args = diagnostic.parser().parse_args(["--api-key", "never-print-me", "--voice-id", "voice-id",
                                            "--output", str(tmp_path / "diagnostic.wav")])
    captured = {}

    class Response:
        status_code = 200
        def read(self): return wav_bytes()
    class Client:
        def __init__(self, **kwargs): captured["client"] = kwargs
        class tts:
            @staticmethod
            def generate(**kwargs):
                captured["request"] = kwargs
                return Response()

    status, count, metadata = diagnostic.generate(args, client_factory=Client)
    assert status == 200 and count > 0 and metadata == (44100, 1, 2, 2)
    assert captured["client"] == {"api_key": "never-print-me", "default_headers": {"cartesia-version": "2026-08-14"}}
    assert captured["request"] == {"model_id": "sonic-3.6", "transcript": "Hello from PHOS",
                                    "voice": {"mode": "id", "id": "voice-id"},
                                    "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
                                    "generation_config": {"speed": 1, "volume": 1}}
    assert "never-print-me" not in capsys.readouterr().out


def test_provider_code_is_sanitized():
    error = type("ApiError", (Exception,), {"body": {"code": "missing_version", "message": "ignored"}})()
    assert diagnostic.provider_code(error) == "missing_version"
    assert diagnostic.provider_code(type("Error", (Exception,), {"code": "not safe!"})()) is None


def test_complete_response_writers_preserve_all_chunks_in_order(tmp_path):
    payload = wav_bytes()
    chunks = (payload[:13], payload[13:41], payload[41:])

    class ChunkedResponse:
        def write_to_file(self, path):
            with Path(path).open("wb") as output:
                for chunk in chunks:
                    output.write(chunk)

    diagnostic_path, provider_path = tmp_path / "diagnostic.wav", tmp_path / "provider.wav"
    assert diagnostic.write_complete_response(ChunkedResponse(), diagnostic_path) == sum(map(len, chunks))
    CartesiaTTSProvider._write_complete_response(ChunkedResponse(), provider_path)
    assert diagnostic_path.read_bytes() == payload
    assert provider_path.read_bytes() == payload


def test_production_provider_matches_diagnostic_sdk_request(tmp_path):
    diagnostic_args = diagnostic.parser().parse_args([
        "--api-key", "diagnostic-secret", "--voice-id", "voice-id", "--output", str(tmp_path / "diagnostic.wav"),
    ])
    captured = {}

    class Response:
        def write_to_file(self, path): Path(path).write_bytes(wav_bytes())

    class DiagnosticClient:
        def __init__(self, **kwargs): captured["diagnostic_client"] = kwargs
        class tts:
            @staticmethod
            def generate(**kwargs):
                captured["diagnostic_request"] = kwargs
                return Response()

    class ProviderClient:
        def __init__(self, **kwargs): captured["provider_client"] = kwargs
        class tts:
            @staticmethod
            def generate(**kwargs):
                captured["provider_request"] = kwargs
                return Response()

    diagnostic.generate(diagnostic_args, client_factory=DiagnosticClient)
    provider = CartesiaTTSProvider("provider-secret", "voice-id", "sonic-3.6", 44100, 1.0, 1.0,
                                   client_factory=ProviderClient)
    audio = provider.synthesize("Hello from PHOS")
    audio.path.unlink()
    assert captured["provider_client"]["default_headers"] == captured["diagnostic_client"]["default_headers"]
    assert captured["provider_request"] == captured["diagnostic_request"]
    assert "provider-secret" not in repr(captured["provider_request"])
