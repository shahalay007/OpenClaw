#!/bin/sh
set -eu

main_pid=""
probe_pid=""

cleanup_pid() {
  pid="$1"
  if [ -n "${pid}" ]; then
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  fi
}

cleanup() {
  cleanup_pid "${probe_pid}"
  cleanup_pid "${main_pid}"
}

start_whatsapp_probe() {
  if [ -z "${WHATSAPP_NUMBER:-}" ]; then
    return
  fi

  (
    initial_delay="${WHATSAPP_PROBE_INITIAL_DELAY_SECONDS:-20}"
    interval="${WHATSAPP_PROBE_INTERVAL_SECONDS:-60}"

    sleep "${initial_delay}" || exit 0

    while kill -0 "${main_pid}" 2>/dev/null; do
      run_probe_with_timeout
      sleep "${interval}" || exit 0
    done
  ) &
  probe_pid=$!
}

run_probe_with_timeout() {
  probe_cmd_pid=""
  probe_timeout_pid=""

  openclaw channels status --probe --json >/dev/null 2>&1 &
  probe_cmd_pid=$!

  (
    sleep "${WHATSAPP_PROBE_TIMEOUT_SECONDS:-30}" || exit 0
    kill "${probe_cmd_pid}" 2>/dev/null || true
  ) &
  probe_timeout_pid=$!

  wait "${probe_cmd_pid}" 2>/dev/null || true
  kill "${probe_timeout_pid}" 2>/dev/null || true
  wait "${probe_timeout_pid}" 2>/dev/null || true
}

trap cleanup INT TERM

node /hostinger/server.mjs &
main_pid=$!

start_whatsapp_probe

wait "${main_pid}"
status=$?

cleanup_pid "${probe_pid}"

exit "${status}"
