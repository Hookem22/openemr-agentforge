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

    echo "Seeding realistic ED-resident sample patients..."
    "${MYSQL_CLI[@]}" "$MYSQLDATABASE" < /var/www/html/docs/seed-sample-patients.sql
fi

# Temporary diagnostics for the "More than one MPM loaded" crash: dump the
# actual runtime env/module state right before handing off to apache2-foreground.
echo "DEBUG: APACHE_* env vars:"; env | grep -i apache || true
echo "DEBUG: apache2ctl -M output:"; apache2ctl -M 2>&1 || true

exec "$@"
