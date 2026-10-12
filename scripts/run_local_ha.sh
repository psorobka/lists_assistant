#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
config_dir="$repo_dir/.test-state/local-ha-docker"
integration_dir="$repo_dir/custom_components/lists_assistant"

mkdir -p "$config_dir"

if [[ ! -f "$config_dir/configuration.yaml" ]]; then
  cat >"$config_dir/configuration.yaml" <<'YAML'
default_config:
homeassistant:
  auth_providers:
    - type: trusted_networks
      trusted_networks:
        - 127.0.0.1
        - ::1
        - 172.17.0.1
      allow_bypass_login: true
    - type: homeassistant
YAML
fi

docker run --rm \
  --name lists-assistant-local-ha \
  --stop-timeout 60 \
  --publish 127.0.0.1:8125:8123 \
  --env TZ=Europe/Warsaw \
  --volume "$config_dir:/config" \
  --volume "$integration_dir:/config/custom_components/lists_assistant:ro" \
  ghcr.io/home-assistant/home-assistant:stable &
container_cli_pid=$!

stop_container() {
  docker stop --timeout 60 lists-assistant-local-ha >/dev/null 2>&1 || true
}

trap stop_container INT TERM
wait "$container_cli_pid"
