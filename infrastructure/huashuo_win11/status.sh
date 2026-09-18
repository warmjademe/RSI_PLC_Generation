#!/usr/bin/env bash
set -euo pipefail

docker ps -a --filter name=plc-win11 \
  --format 'name={{.Names}} status={{.Status}} ports={{.Ports}}'

for port in 8100 8101 8102 8103 33900 33901 33902 33903; do
  if ss -lnt "sport = :$port" | grep -q LISTEN; then
    printf 'port=%s state=listening\n' "$port"
  else
    printf 'port=%s state=closed\n' "$port"
  fi
done

for index in 01 02 03; do
  shared_dir="/home/qyb/plc-win11/shared/terminal-$index"
  printf 'terminal=%s evidence=%s\n' "$index" "$shared_dir"
  for report in terminal-identity-report.json delta-runtime-validation.json; do
    report_path="$shared_dir/$report"
    if [[ -f "$report_path" ]]; then
      printf 'report=%s state=present\n' "$report"
      sed -n \
        -e '/"computer_name"/p' \
        -e '/"worker_id"/p' \
        -e '/"checked_at_local"/p' \
        -e '/"time_zone"/p' \
        -e '/"identity_status"/p' \
        -e '/"status"/p' \
        "$report_path"
    else
      printf 'report=%s state=missing\n' "$report"
    fi
  done
done
