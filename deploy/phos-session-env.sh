#!/bin/sh
# Import the *local* desktop session environment into systemd --user.
#
# This is launched by phos-session-env.desktop from the desktop session, not
# from SSH.  It deliberately refuses non-seat0, remote, inactive, and
# non-graphical logind sessions before changing the user-manager environment.
set -eu

fail() {
    printf '%s\n' "PHOS session bridge: $*" >&2
    exit 0
}

session_id=${XDG_SESSION_ID:-}
[ -n "$session_id" ] || fail "no logind session id; not importing display environment"

property() {
    loginctl show-session "$session_id" --property="$1" --value 2>/dev/null
}

[ "$(property User)" = "$(id -u)" ] || fail "session user does not match service user"
[ "$(property Active)" = "yes" ] || fail "session is not active"
[ "$(property Remote)" = "no" ] || fail "session is remote; refusing SSH environment"
[ "$(property Seat)" = "seat0" ] || fail "session is not attached to local seat0"

session_type=$(property Type)
case "$session_type" in
    x11|wayland) ;;
    *) fail "session type is not graphical" ;;
esac

# A normal local X display is a numeric local display.  Forwarded X11 values
# include a host (for example localhost:10.0), and must never become PHOS's
# robot-face target.  Wayland uses its session-provided socket name.
case "$session_type:${DISPLAY:-}" in
    x11::[0-9]*|wayland:wayland-*) ;;
    *) fail "session has no local graphical display; refusing import" ;;
esac

export PHOS_LOCAL_GRAPHICAL_SESSION=1
export PHOS_DISPLAY_SOURCE=local-seat0
systemctl --user import-environment \
    DISPLAY XAUTHORITY XDG_SESSION_TYPE WAYLAND_DISPLAY \
    DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR \
    PHOS_LOCAL_GRAPHICAL_SESSION PHOS_DISPLAY_SOURCE
systemctl --user start phos.service
