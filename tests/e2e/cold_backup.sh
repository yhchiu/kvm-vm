#!/bin/bash
# Runs only in the disposable source/blank recovery guests of recovery_drill.py.
set -euo pipefail
umask 077
action=${1:?backup or restore}
bundle=${2:?absolute private bundle directory}
config=/etc/kvm-vm/central
compose=(docker compose --project-directory "$config" -f "$config/compose.yaml")
volumes=(kvm-vm-central_vm_data kvm-vm-central_grafana_data)
[[ $EUID == 0 && $bundle == /root/dr-*/bundle ]] || exit 2
case "$action" in
backup)
  mkdir -m 0700 "$bundle"
  # Refuse a partial deployment. Capture mount roots before stopping writers.
  for service in victoriametrics grafana inventory alertmanager vmalert; do
    [[ -n $( "${compose[@]}" ps --status running -q "$service") ]]
  done
  paths=()
  for volume in "${volumes[@]}"; do
    paths+=("$(docker volume inspect --format '{{.Mountpoint}}' "$volume")")
  done
  # Confirm these are the volumes actually attached to the source containers.
  for entry in 'victoriametrics /storage 0' 'grafana /var/lib/grafana 1'; do
    read -r service mount idx <<< "$entry"
    id=$("${compose[@]}" ps -q "$service")
    actual=$(docker inspect --format "{{range .Mounts}}{{if eq .Destination \"$mount\"}}{{.Name}}{{end}}{{end}}" "$id")
    [[ $actual == "${volumes[idx]}" ]]
  done
  trap '"${compose[@]}" start >&2' EXIT
  "${compose[@]}" stop >&2
  tar -C "$config" -cf "$bundle/config.tar" .
  for idx in 0 1; do
    tar --numeric-owner -C "${paths[idx]}" -cf "$bundle/volume-$idx.tar" .
  done
  (cd "$bundle" && sha256sum config.tar volume-0.tar volume-1.tar > SHA256SUMS)
  ;;
restore)
  # Only restore a trusted operator-created, verified bundle onto a blank target.
  [[ ! -e $config && ! -L $config ]]
  [[ -z $(docker ps -aq --filter label=com.docker.compose.project=kvm-vm-central) ]]
  for volume in "${volumes[@]}"; do
    [[ -z $(docker volume ls -q --filter "name=^${volume}$") ]]
  done
  (cd "$bundle" && sha256sum -c SHA256SUMS)
  install -d -m 0700 "$config"
  tar -C "$config" -xf "$bundle/config.tar"
  for idx in 0 1; do
    volume=${volumes[idx]}
    key=${volume#kvm-vm-central_}
    docker volume create --label com.docker.compose.project=kvm-vm-central \
      --label "com.docker.compose.volume=$key" "$volume" >/dev/null
    path=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
    tar --numeric-owner -C "$path" -xf "$bundle/volume-$idx.tar"
  done
  "${compose[@]}" up -d >&2
  ;;
*) exit 2 ;;
esac
