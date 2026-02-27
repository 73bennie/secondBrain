#!/usr/bin/env bash
set -euo pipefail

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
