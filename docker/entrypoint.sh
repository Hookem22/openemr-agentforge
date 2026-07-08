#!/bin/bash
# Railway container entrypoint (Stage 2 exploratory deployment).
#
# Regenerates sites/default/sqlconf.php from the Railway-provided MySQL service
# variables on every boot, waits for the database to accept connections, and — only
# if the schema hasn't been installed yet (detected by querying for patient_data,
# since the container filesystem is rebuilt on every deploy but the external Railway
# MySQL database persists) — runs OpenEMR's unattended installer and seeds the
# realistic ED-resident sample patients used for USER.md's use cases.
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

# Note: a successful `SELECT ... LIMIT 1` here just means the table exists (query
# ran without error), not that it has any rows — checked separately below so an
# existing-but-empty schema (e.g. installer ran but seeding didn't) still gets seeded.
if "${MYSQL_CLI[@]}" "$MYSQLDATABASE" -e "SELECT 1 FROM patient_data LIMIT 1" >/dev/null 2>&1; then
    echo "OpenEMR schema already present, skipping installer."
else
    echo "Running OpenEMR unattended installer..."
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

# Same row-count-vs-query-success lesson as the patient seed check below: a prior
# crash-loop cycle could have interrupted the installer mid-way, leaving the schema
# present but the initial admin account missing. Recreate it (matching
# library/classes/Installer.class.php::add_initial_user()'s defaults) whenever the
# users table is empty, independent of whether the installer ran this boot.
USER_COUNT=$("${MYSQL_CLI[@]}" -N "$MYSQLDATABASE" -e "SELECT COUNT(*) FROM users" 2>/dev/null || echo 0)
if [[ "$USER_COUNT" -eq 0 ]]; then
    echo "No admin user found, recreating initial admin account..."
    ADMIN_HASH=$(su -m www-data -s /bin/bash -c "php -r \"echo password_hash('pass', PASSWORD_DEFAULT);\"")
    "${MYSQL_CLI[@]}" "$MYSQLDATABASE" <<SQL
INSERT INTO \`groups\` (id, name, user) VALUES (1, 'Default', 'admin');
INSERT INTO users (id, username, password, authorized, lname, fname, facility_id, calendar, cal_ui)
    VALUES (1, 'admin', 'NoLongerUsed', 1, 'Administrator', '', 3, 1, 3);
INSERT INTO users_secure (id, username, password, last_update_password)
    VALUES (1, 'admin', '${ADMIN_HASH}', NOW());
SQL
else
    echo "Admin user already present ($USER_COUNT rows), skipping."
fi

PATIENT_COUNT=$("${MYSQL_CLI[@]}" -N "$MYSQLDATABASE" -e "SELECT COUNT(*) FROM patient_data" 2>/dev/null || echo 0)
if [[ "$PATIENT_COUNT" -eq 0 ]]; then
    echo "Seeding realistic ED-resident sample patients..."
    "${MYSQL_CLI[@]}" "$MYSQLDATABASE" < /var/www/html/docs/seed-sample-patients.sql
else
    echo "Sample patients already present ($PATIENT_COUNT rows), skipping seed."
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
