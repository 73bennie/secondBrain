#!/usr/bin/env bash
set -euo pipefail

# Load user config if present
CONFIG="$HOME/.secondbrain/config.sh"
[[ -f "$CONFIG" ]] && source "$CONFIG"

sb_vault_note_path() {
  local type="${1:?entity_type required (ideas/projects/admin/people/...)}"
  local id="${2:?entity_id required}"

  : "${SB_VAULT:?SB_VAULT is not set (check ~/.secondbrain/config.sh)}"
  : "${SB_VAULT_DIR:?SB_VAULT_DIR is not set (check ~/.secondbrain/config.sh)}"

  # Canonical path: <vault>/<dir>/<type>/<id>.md
  printf '%s/%s/%s/%s.md\n' \
    "${SB_VAULT%/}" \
    "${SB_VAULT_DIR#/}" \
    "$type" \
    "$id"
}

sb_print_summary() {
  local DB="$1"

  read unprocessed needs_review active_projects open_admin people_count ideas_count < <(
    sqlite3 -separator ' ' "$DB" "
    SELECT
      (SELECT COUNT(*) FROM inbox WHERE status='unprocessed'),
      (SELECT COUNT(*) FROM inbox WHERE status='needs_review'),
      (SELECT COUNT(*) FROM projects WHERE status IN ('active','waiting','blocked')),
      (SELECT COUNT(*) FROM admin WHERE status='open'),
      (SELECT COUNT(*) FROM people),
      (SELECT COUNT(*) FROM ideas);
    "
  )

  printf "  %-16s %5s\n" "Unprocessed:"     "$unprocessed"
  printf "  %-16s %5s\n" "Needs Review:"    "$needs_review"
  printf "  %-16s %5s\n" "Active Projects:" "$active_projects"
  printf "  %-16s %5s\n" "Open Admin:"      "$open_admin"
  printf "  %-16s %5s\n" "People:"          "$people_count"
  printf "  %-16s %5s\n" "Ideas:"           "$ideas_count"
}
