#!/usr/bin/env bash
set -euo pipefail

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runtime_root=/home/qyb/plc-win11
template_dir="$runtime_root/template"
secret_file="$runtime_root/.env"

if docker ps --format '{{.Names}}' | grep -Fxq plc-win11-template; then
  echo 'Refusing to clone a running template. Shut Windows down cleanly first.' >&2
  exit 1
fi

if [[ ! -f "$template_dir/data.qcow2" ]]; then
  echo "Template disk is missing: $template_dir/data.qcow2" >&2
  exit 1
fi

for index in 01 02 03; do
  target_dir="$runtime_root/terminal-$index"
  if [[ -e "$target_dir" ]]; then
    echo "Refusing to overwrite existing terminal storage: $target_dir" >&2
    exit 1
  fi
done

for index in 01 02 03; do
  target_dir="$runtime_root/terminal-$index"
  shared_dir="$runtime_root/shared/terminal-$index"
  install -d -m 0750 "$target_dir" "$shared_dir"
  cp -a --reflink=auto --sparse=always "$template_dir/." "$target_dir/"
  printf '{\n  "computer_name": "PLC-WIN11-%s",\n  "worker_id": "huashuo-%s"\n}\n' \
    "$index" "$index" >"$shared_dir/terminal-identity.json"
  chmod 0644 "$shared_dir/terminal-identity.json"
  if [[ -f "$runtime_root/shared/template/delta-install-report.json" ]]; then
    cp -a "$runtime_root/shared/template/delta-install-report.json" \
      "$shared_dir/template-delta-install-report.json"
  fi
done

docker compose \
  --env-file "$secret_file" \
  -f "$deploy_dir/compose-terminals.yaml" \
  up -d

docker ps --filter name=plc-win11- \
  --format 'name={{.Names}} status={{.Status}} ports={{.Ports}}'
