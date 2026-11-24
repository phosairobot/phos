# SPDX-License-Identifier: Apache-2.0
"""MPU-6050 register adapter for GY-521 boards over optional smbus2."""
from __future__ import annotations

import logging

from robot.sensors import IMUReading

logger = logging.getLogger(__name__)


class MPU6050Provider:
    """Read the MPU-6050 at its ±2 g / ±250 °/s factory-scale settings.

    The board remains uncalibrated for static mounting bias: automatic offset
    estimation is deliberately avoided because it would require PHOS to be
    motionless during every start.
    """

    available_measurements = (
        "acceleration_x_m_s2", "acceleration_y_m_s2", "acceleration_z_m_s2",
        "angular_velocity_x_deg_s", "angular_velocity_y_deg_s", "angular_velocity_z_deg_s",
    )
    _WHO_AM_I = 0x75
    _PWR_MGMT_1 = 0x6B
    _ACCEL_CONFIG = 0x1C
    _GYRO_CONFIG = 0x1B
    _ACCEL_XOUT_H = 0x3B
    _STANDARD_GRAVITY = 9.80665

    def __init__(self, *, address: int):
        self._address = address
        self._bus = None
        self._started = False

    def start(self):
        from smbus2 import SMBus

        phase = "open bus"
        try:
            self._bus = SMBus(1)
            phase = "read WHO_AM_I"
            identity = self._bus.read_byte_data(self._address, self._WHO_AM_I)
            # AD0 selects 0x68/0x69 on the bus but is not reflected by WHO_AM_I.
            if identity != 0x68:
                raise ValueError(f"WHO_AM_I=0x{identity:02x}, expected 0x68")
            phase = "wake device"
            self._bus.write_byte_data(self._address, self._PWR_MGMT_1, 0x00)
            phase = "set accelerometer range"
            self._bus.write_byte_data(self._address, self._ACCEL_CONFIG, 0x00)
            phase = "set gyroscope range"
            self._bus.write_byte_data(self._address, self._GYRO_CONFIG, 0x00)
            self._started = True
            logger.info("MPU-6050 initialized at 0x%02x (factory scale; no offset calibration)", self._address)
        except Exception as error:
            self.close()
            if isinstance(error, (OSError, ValueError)):
                raise type(error)(f"MPU-6050 at 0x{self._address:02x}, {phase}: {error}") from error
            raise

    @staticmethod
    def _signed(high, low):
        value = (high << 8) | low
        return value - 65536 if value >= 32768 else value

    def read(self):
        if not self._started:
            raise RuntimeError("MPU-6050 is not initialized")
        data = self._bus.read_i2c_block_data(self._address, self._ACCEL_XOUT_H, 14)
        if len(data) != 14:
            raise OSError("MPU-6050 returned an incomplete sample")
        values = tuple(self._signed(data[index], data[index + 1]) for index in range(0, 14, 2))
        return IMUReading(
            *(value / 16384.0 * self._STANDARD_GRAVITY for value in values[:3]),
            *(value / 131.0 for value in values[4:7]),
        )

    def close(self):
        bus, self._bus = self._bus, None
        self._started = False
        if bus is not None:
            bus.close()
