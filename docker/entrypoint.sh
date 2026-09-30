#!/bin/sh
# First boot: seed the code checkout. Every boot: point the Stop hook at it and
# write nginx's config. Then hand over to `zipper <cmd>` (default: run).
set -e
mkdir -p /zipper/config /zipper/home /zipper/vault
URL="${ZIPPER_CODE_URL:-https://github.com/4b3c/Zipper.git}"
if [ ! -d /zipper/code/.git ]; then
    # From upstream, so `zipper update` has history to move along. Offline, the
    # copy in the image, as a one-commit repo pointed at upstream for later.
    if ! git clone -q --branch "${ZIPPER_CODE_BRANCH:-main}" "$URL" /zipper/code; then
        echo "zipper: cannot reach $URL -- seeding from the image" >&2
        cp -a /opt/zipper-seed/. /zipper/code/
        git -C /zipper/code init -q -b main
        git -C /zipper/code add -A
        git -C /zipper/code -c user.name=zipper -c user.email=zipper@zipper.local \
            commit -q -m "seed from image"
        git -C /zipper/code remote add origin "$URL"
    fi
fi
# The volumes are owned by whoever created them on the host.
git config --global --add safe.directory '*'
cd /zipper/code
python3 -m zipper setup --hook >/dev/null
python3 /zipper/code/docker/nginx.py > /etc/nginx/conf.d/zipper.conf
rm -f /etc/nginx/sites-enabled/default
[ -f "$ZIPPER_SETTINGS" ] || echo "zipper: no settings yet -- run: docker compose run --rm <name> setup" >&2
exec python3 -m zipper "$@"
