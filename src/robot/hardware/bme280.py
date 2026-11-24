# SPDX-License-Identifier: Apache-2.0
"""Optional BME280 I2C adapter; vendor imports and bus ownership stay here."""
import logging

from robot.sensors import EnvironmentalReading

logger = logging.getLogger(__name__)


class BME280Provider:
    available_measurements = ("temperature_c", "humidity_percent", "pressure_hpa")

    def __init__(self, *, address: int):
        self._address = address
        self._bus = None
        self._driver = None
        self._calibration = None

    def start(self):
        import bme280
        from smbus2 import SMBus

        logger.debug("Initializing BME280 on I2C bus 1 at 0x%02x", self._address)
        try:
            self._bus = SMBus(1)
            # BMP280 lacks humidity; do not accept it as a BME280.
            if self._bus.read_byte_data(self._address, 0xD0) != 0x60:
                raise ValueError("I2C device is not a BME280")
            self._calibration = bme280.load_calibration_params(self._bus, self._address)
            self._driver = bme280
        except Exception:
            self.close()
            raise

    def read(self):
        if self._bus is None or self._driver is None:
            raise RuntimeError("BME280 is not initialized")
        sample = self._driver.sample(self._bus, self._address, self._calibration)
        reading = EnvironmentalReading(sample.temperature, sample.humidity, sample.pressure)
        if not -40 <= reading.temperature_c <= 85 or not 300 <= reading.pressure_hpa <= 1100:
            raise ValueError("BME280 reading is outside its measurement range")
        return reading

    def close(self):
        bus, self._bus = self._bus, None
        self._driver = self._calibration = None
        if bus is not None:
            bus.close()