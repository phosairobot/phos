#!/usr/bin/env bash
# Install the complete supported PHOS runtime on Raspberry Pi OS.
set -euo pipefail

readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly VENV="$PROJECT_ROOT/.venv"
readonly MODEL_DIR="$PROJECT_ROOT/models/expression"
readonly MODEL_NAME="facial_expression_recognition_mobilefacenet_2022july.onnx"
readonly MODEL_PATH="$MODEL_DIR/$MODEL_NAME"
readonly MODEL_URL="https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/facial_expression_recognition/$MODEL_NAME"
readonly MODEL_SHA256="4f61307602fc089ce20488a31d4e4614e3c9753a7d6c41578c854858b183e1a9"

fail() {
    printf 'PHOS installer: %s\n' "$*" >&2
    exit 1
}

validate_platform() {
    command -v apt-get >/dev/null || fail "Raspberry Pi OS/Debian apt-get is required."
    [[ -r /etc/os-release ]] || fail "Cannot identify the operating system."
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == "raspbian" || "${ID:-}" == "debian" || "${ID:-}" == "ubuntu" || "${ID_LIKE:-}" == *debian* ]] \
        || fail "Supported platform is Raspberry Pi OS (Debian-based)."
    case "$(uname -m)" in
        armv7l|aarch64) ;;
        *) fail "Supported processor architectures are armv7l and aarch64; found $(uname -m)." ;;
    esac
    command -v python3 >/dev/null || fail "python3 is required."
}

install_system_packages() {
    local -a packages=(
        build-essential ca-certificates git i2c-tools libcap-dev opencv-data python3-dev
        python3-opencv python3-picamera2 python3-pyaudio python3-smbus python3-tk python3-venv
        rpicam-apps wget
    )
    local sudo_cmd=()
    if [[ $EUID -ne 0 ]]; then
        command -v sudo >/dev/null || fail "sudo is required to install system packages."
        sudo_cmd=(sudo)
    fi
    "${sudo_cmd[@]}" apt-get update
    "${sudo_cmd[@]}" apt-get install -y "${packages[@]}"
}

prepare_model() {
    mkdir -p "$MODEL_DIR"
    if [[ -f "$MODEL_PATH" ]] && printf '%s  %s\n' "$MODEL_SHA256" "$MODEL_PATH" | sha256sum --check --status; then
        printf 'Local ONNX model already verified: %s\n' "$MODEL_PATH"
        return
    fi
    rm -f "$MODEL_PATH"
    local temporary_path="$MODEL_PATH.download"
    rm -f "$temporary_path"
    wget -O "$temporary_path" "$MODEL_URL"
    printf '%s  %s\n' "$MODEL_SHA256" "$temporary_path" | sha256sum --check
    mv "$temporary_path" "$MODEL_PATH"
}

smoke_check() {
    "$VENV/bin/python" -c '
import tkinter
import RPi.bme280
import boto3
import bmp280
import cv2
import flask
import rpi_ws281x
import smbus2
from picamera2 import Picamera2
from robot import __version__
from robot.config import RuntimeConfig
RuntimeConfig.from_file()
print(f"PHOS {__version__} software imports and configuration are ready; OpenCV {cv2.__version__}")
'
    "$VENV/bin/python" tools/check_opencv.py
    "$VENV/bin/pip" check
}

main() {
    validate_platform
    cd "$PROJECT_ROOT"
    install_system_packages
    if [[ ! -x "$VENV/bin/python" ]]; then
        python3 -m venv --system-site-packages "$VENV"
    fi
    "$VENV/bin/python" -m pip install --upgrade pip setuptools wheel
    "$VENV/bin/pip" install -e '.[all]'
    prepare_model
    smoke_check
    cat <<SUMMARY

PHOS installation complete.
  Project: $PROJECT_ROOT
  Environment: $VENV
  Python runtime extras: all (web, vision, AWS, environmental, CCS811, IMU, LED ring)
  Local ONNX model: $MODEL_PATH

Edit config/phos.yaml, then start a foreground check with:
  .venv/bin/python src/robot/main.py

Hardware wiring, I2C/SPI enablement, camera/display checks, and systemd setup
remain manual steps documented in docs/installation.md.
SUMMARY
}

main "$@"
