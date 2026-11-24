# SPDX-License-Identifier: Apache-2.0
"""Optional BMP280 adapter using the same worker-owned SMBus boundary."""
from robot.sensors import EnvironmentalReading


class BMP280Provider:
    available_measurements = ("temperature_c", "pressure_hpa")

    def __init__(self, *, address: int):
        self._address = address
        self._bus = None
        self._driver = None

    def start(self):
        from bmp280 import BMP280
        from smbus2 import SMBus

        try:
            self._bus = SMBus(1)
            if self._bus.read_byte_data(self._address, 0xD0) != 0x58:
                raise ValueError("I2C device is not a BMP280")
            self._driver = BMP280(i2c_addr=self._address, i2c_dev=self._bus)
            self._driver.setup(mode="forced")
        except Exception:
            self.close()
            raise

    def read(self):
        if self._driver is None:
            raise RuntimeError("BMP280 is not initialized")
        # One conversion supplies both compensated values, already in °C/hPa.
        self._driver.update_sensor()
        reading = EnvironmentalReading(self._driver.temperature, None, self._driver.pressure)
        if not -40 <= reading.temperature_c <= 85 or not 300 <= reading.pressure_hpa <= 1100:
            raise ValueError("BMP280 reading is outside its measurement range")
        return reading

    def close(self):
        bus, self._bus = self._bus, None
        self._driver = None
        if bus is not None:
            bus.close()