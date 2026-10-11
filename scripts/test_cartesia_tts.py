#!/usr/bin/env python3
"""Make one direct Cartesia TTS request for development diagnostics only."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import wave


CARTESIA_API_VERSION = "2026-08-14"
DEFAULT_MODEL_ID = "sonic-3.6"
DEFAULT_OUTPUT = Path("/tmp/phos-cartesia-test.wav")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--api-key", default=os.environ.get("CARTESIA_API_KEY"),
                        help="Cartesia API key; defaults to CARTESIA_API_KEY (never printed).")
    result.add_argument("--voice-id", required=True)
    result.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    result.add_argument("--api-version", default=CARTESIA_API_VERSION)
    result.add_argument("--text", default="Hello from PHOS")
    result.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return result


def request_arguments(args: argparse.Namespace) -> dict:
    """The SDK request body arguments; kept visible for offline inspection/tests."""
    return {
        "model_id": args.model_id,
        "transcript": args.text,
        # Cartesia SDK 3.5.1's typed request contract represents an ID voice
        # with this object, even though raw REST examples may show a string.
        "voice": {"mode": "id", "id": args.voice_id},
        "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 44100},
        "generation_config": {"speed": 1, "volume": 1},
    }


def provider_code(error: Exception) -> str | None:
    body = getattr(error, "body", None)
    code = body.get("code") if isinstance(body, dict) else getattr(error, "code", None)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_-]{1,64}", code) else None


def write_complete_response(response, output: Path) -> int:
    """Persist all SDK/iterator audio chunks, without retaining only a final chunk."""
    writer = getattr(response, "write_to_file", None)
    if callable(writer):
        writer(output)
    else:
        reader = getattr(response, "read", None)
        if callable(reader):
            payload = reader()
            if not isinstance(payload, bytes):
                raise TypeError("Cartesia response read() did not return bytes.")
            output.write_bytes(payload)
        elif isinstance(response, bytes):
            output.write_bytes(response)
        else:
            with output.open("wb") as artifact:
                for chunk in response:
                    artifact.write(chunk)
    return output.stat().st_size


def generate(args: argparse.Namespace, *, client_factory=None) -> tuple[int | None, int, tuple[int, int, int, int]]:
    """Run the SDK request and save the returned WAV; no PHOS runtime involved."""
    if not args.api_key:
        raise ValueError("Set CARTESIA_API_KEY or pass --api-key.")
    if client_factory is None:
        from cartesia import Cartesia
        client_factory = Cartesia
    client = client_factory(api_key=args.api_key,
                            default_headers={"cartesia-version": args.api_version})
    response = client.tts.generate(**request_arguments(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    byte_count = write_complete_response(response, args.output)
    with wave.open(str(args.output), "rb") as artifact:
        metadata = (artifact.getframerate(), artifact.getnchannels(), artifact.getsampwidth(), artifact.getnframes())
    return getattr(response, "status_code", None), byte_count, metadata


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        status, byte_count, (rate, channels, width, frames) = generate(args)
    except ValueError as error:
        print(f"Cartesia diagnostic error: {error}", file=sys.stderr)
        return 2
    except ImportError:
        print("Cartesia diagnostic error: the Cartesia SDK is not installed. Run .venv/bin/pip install -e '.[all]'.",
              file=sys.stderr)
        return 2
    except Exception as error:
        status = getattr(error, "status_code", None)
        code = provider_code(error)
        detail = f" provider_code={code}" if code else ""
        print(f"Cartesia diagnostic failure: status={status if status is not None else 'unavailable'}{detail}", file=sys.stderr)
        return 1
    duration = frames / rate if rate else 0
    print(f"Cartesia diagnostic success: status={status if status is not None else 'SDK success'} bytes={byte_count}")
    print(f"Saved WAV: {args.output}")
    print(f"WAV: sample_rate={rate} channels={channels} sample_width={width} frames={frames} duration_seconds={duration:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
