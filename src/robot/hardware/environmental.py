# SPDX-License-Identifier: Apache-2.0
"""Central selection of environmental adapters and their capabilities."""
from robot.hardware.bme280 import BME280Provider
from robot.hardware.bmp280 import BMP280Provider


def environmental_provider_type(sensor_type):
    providers = {"bme280": BME280Provider, "bmp280": BMP280Provider}
    try:
        return providers[sensor_type]
    except KeyError:
        raise ValueError(f"Unsupported environmental sensor type: {sensor_type}") from None
## End