npx @modelcontextprotocol/inspector \
  ssh -T pi@192.168.1.127 \
  bash -lc '"cd /home/pi/phos && \
  export DISPLAY=:0 && \
  export XAUTHORITY=/home/pi/.Xauthority && \
  export XDG_RUNTIME_DIR=/run/user/1000 && \
  export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus && \
  export XDG_SESSION_TYPE=x11 && \
  export DESKTOP_SESSION=rpd-x && \
  export PHOS_DISPLAY_SOURCE=local-seat0 && \
  export PHOS_LOCAL_GRAPHICAL_SESSION=1 && \
  export PYTHONPATH=/home/pi/phos/src && \
  exec /home/pi/phos/.venv/bin/python -m robot.mcp --config /home/pi/phos/config/phos.yaml"'