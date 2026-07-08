[![Syntax Status](https://github.com/openemr/openemr/actions/workflows/syntax.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/syntax.yml)
[![Styling Status](https://github.com/openemr/openemr/actions/workflows/styling.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/styling.yml)
[![Testing Status](https://github.com/openemr/openemr/actions/workflows/test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/test.yml)
[![JS Unit Testing Status](https://github.com/openemr/openemr/actions/workflows/js-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/js-test.yml)
[![PHPStan](https://github.com/openemr/openemr/actions/workflows/phpstan.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/phpstan.yml)
[![Rector](https://github.com/openemr/openemr/actions/workflows/rector.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/rector.yml)
[![ShellCheck](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/shellcheck.yml)
[![Docker Compose Linting](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-compose-lint.yml)
[![Dockerfile Linting](https://github.com/openemr/openemr/actions/workflows/docker-lint-hadolint.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/docker-lint-hadolint.yml)
[![Isolated Tests](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/isolated-tests.yml)
[![Inferno Certification Test](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/inferno-test.yml)
[![Composer Checks](https://github.com/openemr/openemr/actions/workflows/composer.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer.yml)
[![Composer Require Checker](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/composer-require-checker.yml)
[![API Docs Freshness Checks](https://github.com/openemr/openemr/actions/workflows/api-docs.yml/badge.svg)](https://github.com/openemr/openemr/actions/workflows/api-docs.yml)
[![codecov](https://codecov.io/gh/openemr/openemr/graph/badge.svg?token=7Eu3U1Ozdq)](https://codecov.io/gh/openemr/openemr)

[![Backers on Open Collective](https://opencollective.com/openemr/backers/badge.svg)](#backers) [![Sponsors on Open Collective](https://opencollective.com/openemr/sponsors/badge.svg)](#sponsors)

# OpenEMR

[OpenEMR](https://open-emr.org) is a Free and Open Source electronic health records and medical practice management application. It features fully integrated electronic health records, practice management, scheduling, electronic billing, internationalization, free support, a vibrant community, and a whole lot more. It runs on Windows, Linux, Mac OS X, and many other platforms.

## AgentForge: Clinical Co-Pilot (this fork)

This is a fork of OpenEMR used to build the "Clinical Co-Pilot" agent project (Gauntlet AI, AgentForge). See
[`USER.md`](USER.md) for the target user and use cases, [`AUDIT.md`](AUDIT.md) for the security/compliance
audit, and [`ARCHITECTURE.md`](ARCHITECTURE.md) for the agent integration plan.

### Local development setup (macOS, native Homebrew LAMP)

No Docker is used for local dev in this fork; everything runs natively via Homebrew.

1. **Install the stack:**
   ```shell
   brew install php@8.2 mariadb composer httpd
   brew unlink php && brew link --overwrite --force php@8.2   # composer pulls in a newer php as a dependency
   brew services start mariadb
   brew services start httpd
   ```

2. **Create the database** (matches the credentials already in `sites/default/sqlconf.php`):
   ```shell
   mariadb -u "$(whoami)" <<'EOF'
   CREATE DATABASE openemr;
   CREATE USER 'openemr'@'localhost' IDENTIFIED BY 'openemr';
   GRANT ALL PRIVILEGES ON openemr.* TO 'openemr'@'localhost';
   EOF
   ```
   (Homebrew's MariaDB requires unix-socket auth as your own OS user — `mysql -u root` will fail with
   "Access denied"; connect as `mariadb -u $(whoami)` instead.)

3. **Configure Apache** (`/opt/homebrew/etc/httpd/httpd.conf`): enable `rewrite_module` and PHP (point
   `LoadModule php_module` at Homebrew php@8.2's `libphp.so`, add a `<FilesMatch \.php$>` handler block), set
   `DocumentRoot`/`<Directory>` to this repo's path, set `AllowOverride All` (the existing per-directory
   `.htaccess` files need this), and set `DirectoryIndex index.php index.html`. Verify with
   `apachectl configtest`, then `brew services restart httpd`.

4. **Install dependencies and build assets:**
   ```shell
   composer install
   npm install
   npm run build
   ```

5. **Run the unattended installer** (initializes schema + base/demo data):
   ```shell
   OPENEMR_ENABLE_INSTALLER_AUTO=1 php contrib/util/installScripts/InstallerAuto.php \
     no_root_db_access=1 login=openemr pass=openemr dbname=openemr iuserpass=pass
   ```
   Default login afterward is `admin` / `pass`.

6. **Seed realistic ED-resident sample patients** (grounded in `USER.md`'s use cases — one patient with a full
   prior-encounter/allergy/medication/vitals/labs history, one with a thin/empty chart):
   ```shell
   mariadb -u "$(whoami)" openemr < docs/seed-sample-patients.sql
   ```

7. Visit `http://localhost:8080/` (or whatever port/`Listen` directive you configured) and log in.

### Contributing

OpenEMR is a leader in healthcare open source software and comprises a large and diverse community of software developers, medical providers and educators with a very healthy mix of both volunteers and professionals. [Join us and learn how to start contributing today!](https://open-emr.org/wiki/index.php/FAQ#How_do_I_begin_to_volunteer_for_the_OpenEMR_project.3F)

> Already comfortable with git? Check out [CONTRIBUTING.md](CONTRIBUTING.md) for quick setup instructions and requirements for contributing to OpenEMR by resolving a bug or adding an awesome feature 😊.

### Support

Community and Professional support can be found [here](https://open-emr.org/wiki/index.php/OpenEMR_Support_Guide).

Extensive documentation and forums can be found on the [OpenEMR website](https://open-emr.org) that can help you to become more familiar about the project 📖.

### Reporting Issues and Bugs

Report these on the [Issue Tracker](https://github.com/openemr/openemr/issues). If you are unsure if it is an issue/bug, then always feel free to use the [Forum](https://community.open-emr.org/) and [Chat](https://www.open-emr.org/chat/) to discuss about the issue 🪲.

### Reporting Security Vulnerabilities

Check out [SECURITY.md](.github/SECURITY.md)

### API

Check out [API_README.md](API_README.md)

### Docker

Check out [DOCKER_README.md](DOCKER_README.md)

### FHIR

Check out [FHIR_README.md](FHIR_README.md)

### For Developers

If using OpenEMR directly from the code repository, then the following commands will build OpenEMR (Node.js version 24.* is required) :

```shell
composer install --no-dev
npm install
npm run build
composer dump-autoload -o
```

### Contributors

This project exists thanks to all the people who have contributed. [[Contribute]](CONTRIBUTING.md).
<a href="https://github.com/openemr/openemr/graphs/contributors"><img src="https://opencollective.com/openemr/contributors.svg?width=890" /></a>


### Sponsors

Thanks to our [ONC Certification Major Sponsors](https://www.open-emr.org/wiki/index.php/OpenEMR_Certification_Stage_III_Meaningful_Use#Major_sponsors)!


### License

[GNU GPL](LICENSE)
