#!/usr/bin/env bash
# Provisions the VM (Ubuntu 24.04): packages, user, directories, units, Caddy and firewall.
# Safe to run more than once.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "setup.sh must run as root (use sudo)" >&2
    exit 1
fi

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
# The service runs from /opt/trending-site, and ProtectHome hides any copy under /home.
if [ "$DEPLOY_DIR" != /opt/trending-site/deploy ]; then
    echo "setup.sh must run from /opt/trending-site/deploy, not $DEPLOY_DIR:" >&2
    echo "clone the repository into /opt/trending-site" >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive

# systemctl and iptables only work on a booted VM, not in a plain container.
if [ "$(ps -p 1 -o comm= | tr -d ' ')" = "systemd" ]; then
    HAS_SYSTEMD=1
else
    HAS_SYSTEMD=0
fi

# Copies a file with the given mode; reports whether it changed anything.
install_file() {
    local mode="$1" src="$2" dest="$3"
    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        echo "unchanged: $dest"
        return
    fi
    install -D -m "$mode" -o root -g root "$src" "$dest"
    echo "installed: $dest"
}

# Copies an env example only when the target does not exist yet.
seed_env() {
    local src="$1" dest="$2"
    if [ -e "$dest" ]; then
        echo "kept: $dest"
        return
    fi
    install -D -m 0600 -o root -g root "$src" "$dest"
    echo "created: $dest (fill it in)"
}

# --- Packages ---
if ! dpkg -s python3 iptables-persistent >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3 iptables-persistent
else
    echo "present: python3, iptables-persistent"
fi

if ! command -v caddy >/dev/null 2>&1; then
    apt-get update
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        -o /etc/apt/sources.list.d/caddy-stable.list
    chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg \
        /etc/apt/sources.list.d/caddy-stable.list
    apt-get update
    apt-get install -y caddy
else
    echo "present: caddy"
fi

# --- User and directories ---
if ! id trending >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin trending
    echo "created user: trending"
fi
install -d -m 0755 -o trending -g trending /srv/trending-site
install -d -m 0755 -o root -g root /etc/trending-site

# --- Configuration ---
seed_env "$DEPLOY_DIR/trending-site.env.example" /etc/trending-site/env
seed_env "$DEPLOY_DIR/caddy.env.example" /etc/caddy/site.env

install_file 0644 "$DEPLOY_DIR/trending-site.service" /etc/systemd/system/trending-site.service
install_file 0644 "$DEPLOY_DIR/trending-site.timer" /etc/systemd/system/trending-site.timer
install_file 0644 "$DEPLOY_DIR/caddy-env.conf" \
    /etc/systemd/system/caddy.service.d/trending-site.conf
install_file 0644 "$DEPLOY_DIR/Caddyfile" /etc/caddy/Caddyfile

if [ "$HAS_SYSTEMD" -eq 1 ]; then
    systemctl daemon-reload
    systemctl enable --now trending-site.timer
else
    echo "skipping systemctl daemon-reload/enable: systemd is not PID 1 (container?)"
fi

# --- Firewall: ACCEPT before the first REJECT in INPUT ---
if [ "$HAS_SYSTEMD" -eq 1 ]; then
    changed=0
    for port in 80 443; do
        if iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
            echo "present: iptables ACCEPT tcp/$port"
            continue
        fi
        pos="$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" {print $1; exit}')"
        if [ -n "$pos" ]; then
            iptables -I INPUT "$pos" -p tcp --dport "$port" -j ACCEPT
        else
            iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
        fi
        echo "added: iptables ACCEPT tcp/$port"
        changed=1
    done
    if [ "$changed" -eq 1 ]; then
        netfilter-persistent save
    fi
else
    echo "skipping iptables rules for ports 80 and 443: systemd is not PID 1 (container?)"
fi

# --- Caddy ---
# Caddy serves only once the domain, the user and the password hash are all set.
site_ready=1
for var in TRENDING_DOMAIN TRENDING_USER TRENDING_PASSWORD_HASH; do
    grep -Eq "^${var}=.+" /etc/caddy/site.env || site_ready=0
done
if [ "$site_ready" -eq 1 ]; then
    if [ "$HAS_SYSTEMD" -eq 1 ]; then
        systemctl enable caddy
        systemctl restart caddy
        echo "caddy enabled and restarted"
    else
        echo "skipping caddy enable and restart: systemd is not PID 1 (container?)"
    fi
else
    # The package starts and enables Caddy with its welcome page on :80; disabled, it serves
    # nothing until then, a reboot included.
    if [ "$HAS_SYSTEMD" -eq 1 ]; then
        systemctl disable --now caddy
        echo "caddy disabled until /etc/caddy/site.env is filled in"
    else
        echo "skipping caddy disable: systemd is not PID 1 (container?)"
    fi
    echo "Fill in /etc/caddy/site.env (TRENDING_DOMAIN, TRENDING_USER,"
    echo "TRENDING_PASSWORD_HASH from 'caddy hash-password') and run setup.sh again"
fi
if [ "$(grep -Ec '^(GEMINI_API_KEY|GITHUB_TOKEN)=.+' /etc/trending-site/env)" -lt 2 ]; then
    echo "Fill in GEMINI_API_KEY and GITHUB_TOKEN in /etc/trending-site/env"
fi
