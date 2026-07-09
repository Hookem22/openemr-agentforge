#!/bin/bash
# Railway container entrypoint (Stage 2 exploratory deployment).
#
# Regenerates sites/default/sqlconf.php from the Railway-provided MySQL service
# variables on every boot, waits for the database to accept connections, and — only
# if the schema+admin-user setup isn't complete (the container filesystem is rebuilt
# on every deploy but the external Railway MySQL database persists, so a prior
# interrupted install can leave stale, partial state) — resets the database and
# runs OpenEMR's unattended installer, then seeds the realistic ED-resident sample
# patients used for USER.md's use cases.
set -euo pipefail

: "${MYSQLHOST:?MYSQLHOST is required (set via Railway variable reference to the MySQL service)}"
: "${MYSQLPORT:?MYSQLPORT is required}"
: "${MYSQLUSER:?MYSQLUSER is required}"
: "${MYSQLPASSWORD:?MYSQLPASSWORD is required}"
: "${MYSQLDATABASE:?MYSQLDATABASE is required}"

SITE_DIR=/var/www/html/sites/default

cat > "$SITE_DIR/sqlconf.php" <<PHP
<?php
//  OpenEMR
//  MySQL Config (generated at container start from Railway env vars)

\$host  = '${MYSQLHOST}';
\$port  = '${MYSQLPORT}';
\$login = '${MYSQLUSER}';
\$pass  = '${MYSQLPASSWORD}';
\$dbase = '${MYSQLDATABASE}';

\$sqlconf = array();
global \$sqlconf;
\$sqlconf["host"]= \$host;
\$sqlconf["port"] = \$port;
\$sqlconf["login"] = \$login;
\$sqlconf["pass"] = \$pass;
\$sqlconf["dbase"] = \$dbase;

//////////////////////////
//////DO NOT TOUCH THIS///
\$config = 1; /////////////
//////////////////////////
?>
PHP
chown www-data:www-data "$SITE_DIR/sqlconf.php"

# Railway's MySQL uses a self-signed cert; the mysql CLI's default cert verification
# rejects it, so disable SSL for these internal, private-network-only connections.
MYSQL_CLI=(mysql -h "$MYSQLHOST" -P "$MYSQLPORT" -u "$MYSQLUSER" -p"$MYSQLPASSWORD" --skip-ssl)

echo "Waiting for MySQL at ${MYSQLHOST}:${MYSQLPORT}..."
for i in $(seq 1 30); do
    if "${MYSQL_CLI[@]}" -e "SELECT 1" >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

# Note: a successful `SELECT ... LIMIT 1`/`SELECT COUNT(*)` here just means the
# table exists and the query ran without error — not that meaningful data is in
# it. A prior crash-loop cycle could have interrupted the installer mid-way,
# leaving patient_data present but the users table (and, critically, the
# associated phpGACL access-control rows the installer's install_gacl() step
# creates) empty. A partial install like that can't be patched by hand-inserting
# a few rows into users/users_secure — phpGACL's schema (gacl_aro, gacl_aro_groups,
# gacl_acl, etc.) needs many consistent rows built via GaclApi, so treat "schema
# present but no users" as "installation incomplete" and let the full installer
# redo everything from a clean database rather than limping along with an
# inconsistent partial state.
NEEDS_INSTALL=0
if ! "${MYSQL_CLI[@]}" "$MYSQLDATABASE" -e "SELECT 1 FROM patient_data LIMIT 1" >/dev/null 2>&1; then
    NEEDS_INSTALL=1
fi
USER_COUNT=$("${MYSQL_CLI[@]}" -N "$MYSQLDATABASE" -e "SELECT COUNT(*) FROM users" 2>/dev/null || echo 0)
if [[ "$USER_COUNT" -eq 0 ]]; then
    NEEDS_INSTALL=1
fi

if [[ "$NEEDS_INSTALL" -eq 0 ]]; then
    echo "OpenEMR schema and admin user already present, skipping installer."
else
    echo "Schema missing or incomplete (no users found) -- resetting database and running installer..."
    "${MYSQL_CLI[@]}" -e "DROP DATABASE IF EXISTS \`$MYSQLDATABASE\`; CREATE DATABASE \`$MYSQLDATABASE\`;"
    # OpenEMR's RootCliGuard forbids running CLI scripts as root (files would end up
    # owned by root, unreadable by the web server later), so run as www-data via `su -m`
    # (preserves the exported env vars the inner command reads).
    export OPENEMR_ENABLE_INSTALLER_AUTO=1
    export MYSQLHOST MYSQLPORT MYSQLUSER MYSQLPASSWORD MYSQLDATABASE
    su -m www-data -s /bin/bash -c '
        php /var/www/html/contrib/util/installScripts/InstallerAuto.php \
            no_root_db_access=1 \
            server="$MYSQLHOST" \
            port="$MYSQLPORT" \
            login="$MYSQLUSER" \
            pass="$MYSQLPASSWORD" \
            dbname="$MYSQLDATABASE" \
            iuserpass=pass
    '
fi

PATIENT_COUNT=$("${MYSQL_CLI[@]}" -N "$MYSQLDATABASE" -e "SELECT COUNT(*) FROM patient_data" 2>/dev/null || echo 0)
if [[ "$PATIENT_COUNT" -eq 0 ]]; then
    echo "Seeding realistic ED-resident sample patients..."
    "${MYSQL_CLI[@]}" "$MYSQLDATABASE" < /var/www/html/docs/seed-sample-patients.sql
else
    echo "Sample patients already present ($PATIENT_COUNT rows), skipping seed."
fi

# Additional sample patients (Robert Chen, Dorothy Simmons) covering use cases the base seed
# doesn't exercise (clinical-constraint flagging, unrelated-history filtering, verified-absent
# allergy data). Checked independently by name so it seeds exactly once regardless of what state
# the base seed check above found the DB in.
CHEN_COUNT=$("${MYSQL_CLI[@]}" -N "$MYSQLDATABASE" -e "SELECT COUNT(*) FROM patient_data WHERE fname='Robert' AND lname='Chen'" 2>/dev/null || echo 0)
if [[ "$CHEN_COUNT" -eq 0 ]]; then
    echo "Seeding additional sample patients (Robert Chen, Dorothy Simmons)..."
    "${MYSQL_CLI[@]}" "$MYSQLDATABASE" < /var/www/html/docs/seed-additional-patients.sql
else
    echo "Additional sample patients already present, skipping seed."
fi

# Belt-and-suspenders: the baked-in mods-enabled state from the image build has
# been observed to not always match what's present in the actual running
# container on Railway (build-time `apache2ctl -M` shows only mpm_prefork, but
# the deployed container sometimes still has mpm_event/mpm_worker enabled too,
# which Apache refuses to start with). Re-normalize at every boot so startup
# doesn't depend on that being preserved correctly from build to runtime.
a2dismod mpm_event mpm_worker >/dev/null 2>&1 || true
a2enmod mpm_prefork >/dev/null 2>&1 || true

exec "$@"
