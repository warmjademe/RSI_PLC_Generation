#!/usr/bin/env bash
set -euo pipefail

deploy_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
runtime_root=/home/qyb/plc-win11
secret_file="$runtime_root/.env"

install -d -m 0750 \
  "$runtime_root/template" \
  "$runtime_root/shared/template" \
  "$runtime_root/oem"

install -m 0644 "$deploy_dir/Install-DeltaTools.ps1" "$runtime_root/oem/Install-DeltaTools.ps1"
install -m 0644 "$deploy_dir/Configure-TerminalIdentity.ps1" "$runtime_root/oem/Configure-TerminalIdentity.ps1"
install -m 0644 "$deploy_dir/oem-install.bat" "$runtime_root/oem/install.bat"

if [[ ! -f "$secret_file" ]]; then
  umask 077
  rdp_password=$(openssl rand -hex 18)
  printf 'RDP_PASSWORD=%s\n' "$rdp_password" >"$secret_file"
fi
chmod 0600 "$secret_file"

docker compose \
  --env-file "$secret_file" \
  -f "$deploy_dir/compose-template.yaml" \
  up -d

docker ps --filter name=plc-win11-template \
  --format 'name={{.Names}} status={{.Status}} ports={{.Ports}}'
