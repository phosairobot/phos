import serial
import json
import time

port = "/dev/cu.usbmodem14101"

with serial.Serial(port, 115200, timeout=0.5) as ser:
    ser.dtr = True
    time.sleep(2)
    ser.reset_input_buffer()

    commands = [
        {
            "protocol": "phos-hw",
            "version": 1,
            "type": "ping",
            "payload": {},
            "id": "test-ping"
        },
        {
            "protocol": "phos-hw",
            "version": 1,
            "type": "request_capabilities",
            "payload": {},
            "id": "test-capabilities"
        }
    ]

    for command in commands:
        ser.write((json.dumps(command) + "\n").encode())
        ser.flush()
        print("TX:", command["type"])

    deadline = time.monotonic() + 8

    while time.monotonic() < deadline:
        line = ser.readline()
        if line:
            print("RX:", line.decode(errors="replace").strip())
