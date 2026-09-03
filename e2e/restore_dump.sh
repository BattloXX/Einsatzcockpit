#!/usr/bin/env bash
# Spielt einen mariadb-dump in die isolierte Board-E2E-Datenbank ein.
#
#   e2e/restore_dump.sh /pfad/zum/dump.sql.gz
#
# Zwei Eingriffe sind noetig, beide rein technisch:
#
# 1) Generische FK-Namen entfernen. Die Dumps enthalten 81x CONSTRAINT `1`,
#    53x CONSTRAINT `2` usw. InnoDB verlangt FK-Namen eindeutig pro Schema,
#    deshalb bricht ein unveraenderter Restore bei der zweiten Tabelle mit
#    "errno: 121 Duplicate key on write or update" ab. Ohne Namen vergibt
#    MariaDB selbst eindeutige (<tabelle>_ibfk_N) -- die Fremdschluessel
#    bleiben inhaltlich identisch.
# 2) FOREIGN_KEY_CHECKS waehrend des Imports aus, weil die Tabellen in
#    alphabetischer statt topologischer Reihenfolge kommen.
set -euo pipefail

DUMP="${1:?Pfad zum Dump angeben}"
ENV_FILE="$(dirname "$0")/../.env.board-e2e"
DB_CONTAINER="${DB_CONTAINER:-ec-board-e2e-db-1}"
DB_NAME="$(grep '^MARIADB_DATABASE=' "$ENV_FILE" | cut -d= -f2)"
DB_PW="$(grep '^MARIADB_ROOT_PASSWORD=' "$ENV_FILE" | cut -d= -f2)"

echo "Ziel: Container ${DB_CONTAINER}, Datenbank ${DB_NAME}"
docker exec -i "$DB_CONTAINER" mariadb -uroot -p"$DB_PW" \
  -e "DROP DATABASE IF EXISTS \`${DB_NAME}\`; CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"

{
  echo "SET FOREIGN_KEY_CHECKS=0;"
  echo "SET UNIQUE_CHECKS=0;"
  zcat -f "$DUMP" | sed -E 's/CONSTRAINT `[0-9]+` FOREIGN KEY/FOREIGN KEY/g'
  echo "SET FOREIGN_KEY_CHECKS=1;"
  echo "SET UNIQUE_CHECKS=1;"
} | docker exec -i "$DB_CONTAINER" mariadb -uroot -p"$DB_PW" "$DB_NAME"

echo "Restore fertig. Bestand:"
docker exec -i "$DB_CONTAINER" mariadb -uroot -p"$DB_PW" -N -B "$DB_NAME" -e "
  SELECT 'einsaetze', COUNT(*) FROM incident
  UNION ALL SELECT 'lagen', COUNT(*) FROM major_incident
  UNION ALL SELECT 'stellen', COUNT(*) FROM incident_site
  UNION ALL SELECT 'alembic', version_num FROM alembic_version;"
