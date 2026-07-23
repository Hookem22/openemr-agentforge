# Railway deployment image for the AgentForge Clinical Co-Pilot fork of OpenEMR.
#
# This is an exploratory/MVP deployment image (Stage 2 "Deploy It" — does not need to
# be production-hardened yet). It assumes `composer install` and `npm run build` have
# already been run on the host (vendor/ and built front-end assets are copied in as-is,
# since both are platform-independent PHP/JS output) rather than re-running them inside
# the image, to keep the build fast and avoid needing every optional PHP extension
# (imagick, redis) present at image-build time.
FROM php:8.2-apache

# Railway passes its own RAILWAY_GIT_COMMIT_SHA as a build arg automatically for every git-connected
# build (Dockerfile or not) -- undeclared build args are silently ignored, so it's never reached this
# image until now. Persisted as a real runtime env var (not just a build-time ARG) so
# interface/modules/copilot/version.php can report it -- see that file's comment for why this exists.
ARG RAILWAY_GIT_COMMIT_SHA=unknown
ENV DEPLOYED_COMMIT_SHA=$RAILWAY_GIT_COMMIT_SHA

RUN apt-get update && apt-get install -y --no-install-recommends \
    libzip-dev \
    libpng-dev \
    libjpeg62-turbo-dev \
    libwebp-dev \
    libfreetype6-dev \
    libicu-dev \
    libldap2-dev \
    libxslt1-dev \
    default-mysql-client \
    && rm -rf /var/lib/apt/lists/* \
    && docker-php-ext-configure gd --with-freetype --with-jpeg --with-webp \
    && docker-php-ext-configure ldap --with-libdir=lib/$(uname -m)-linux-gnu \
    && docker-php-ext-install -j"$(nproc)" \
    pdo_mysql \
    mysqli \
    gd \
    intl \
    ldap \
    soap \
    xsl \
    zip \
    bcmath \
    calendar \
    sockets \
    exif \
    opcache \
    && (a2dismod mpm_event mpm_worker || true) \
    && a2enmod mpm_prefork \
    && a2enmod rewrite

# Apache: OpenEMR relies on per-directory .htaccess files, and index.php must take
# priority over index.html.
RUN { \
    echo '<Directory /var/www/html>'; \
    echo '    AllowOverride All'; \
    echo '    Require all granted'; \
    echo '</Directory>'; \
    } > /etc/apache2/conf-available/openemr.conf \
    && a2enconf openemr \
    && sed -i 's/DirectoryIndex .*/DirectoryIndex index.php index.html/' /etc/apache2/mods-available/dir.conf

WORKDIR /var/www/html
COPY --chown=www-data:www-data . .

RUN chmod +x docker/entrypoint.sh

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["apache2-foreground"]
