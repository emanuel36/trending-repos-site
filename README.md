# Trending repos site

A password-protected website with the GitHub repositories that are trending today and that you
have not seen yet. Every morning a job reads the Trendshift daily ranking and the GitHub
Trending daily ranking, summarizes each README in Portuguese with Gemini, and publishes one page
per day. A repo is listed once and never comes back, so the page shows only what is new.

The idea comes from the trending-repos Claude Code skill. This project does the same job on a
free server, without depending on your own machine being on.

## What each page shows

- **Repos of the day:** GitHub numbers, the first README image and a summary in Portuguese,
  sorted by stars. A collapsed row shows the first sentence of what the repo is.
- **Summary fields:** what it is, who it is for, alternatives, maturity and, when the README is
  not in Portuguese or English, the language it was translated from.
- **Run report:** how many repos, from which sources, which source failed (if any) and how many
  repos are waiting in the queue.
- **Index:** links to every past day, newest first. When the last run failed, the index says
  when and why.

## How it works

- **Schedule:** the job runs every day at 07:00 São Paulo time. If the VM was off at that time,
  it runs at the next boot.
- **Model:** summaries use the Gemini model gemini-3.5-flash-lite by default. Set GEMINI_MODEL
  in the secrets file to use another one.
- **Limits:** at most 30 repos per day. Repos left over by the daily Gemini quota or by the
  30-repo cap wait in a queue for the next day.
- **Failed summaries:** a repo whose own summary fails is retried on up to three separate days.
  After that it is published with only its GitHub description.
- **Serving:** Caddy serves the pages over HTTPS with a login and password on every path.

## How the repo is organized

Repository: https://github.com/emanuel36/trending-repos-site

    trending-repos-site
    ├── trending_site/   the job: fetch sources, summarize, render pages, keep the history
    ├── deploy/          VM setup script, systemd service and timer, Caddy configuration
    ├── tests/           automated tests for the job
    └── docs/            design and implementation plan

## One-time setup

Setup has a part you do by hand, because it needs accounts and a credit card, and a part the
setup script does for you.

### 1. Oracle Cloud VM

- **Account:** create an Oracle Cloud account. It asks for a credit card to verify identity.
- **VM:** create an Always Free VM running Ubuntu 24.04 and add your SSH key.
- **Public IP:** reserve the VM's public IP, so that it does not change when the VM restarts.
- **Ports:** in the VCN security list of the VM's subnet, add ingress rules that allow TCP
  ports 80 and 443 from anywhere. The VM's own firewall is opened by the setup script.

### 2. DuckDNS subdomain

Create a free subdomain at https://www.duckdns.org and point it at the reserved public IP. The
IP is fixed, so this is done once and nothing needs to update it later.

### 3. Keys

- **Gemini key:** create an API key in Google AI Studio (https://aistudio.google.com). The free
  tier is enough.
- **GitHub token:** create a fine-grained personal access token with no scopes and no extra
  permissions. It only raises the GitHub API rate limit, which a day of work would otherwise
  exceed. The token is required: without it every run fails and the index shows
  "token do GitHub ausente".
- **Token expiry:** fine-grained tokens expire, after 30 days by default. Choose the longest
  expiry offered, or no expiry. Once the token expires, the index shows "token do GitHub
  rejeitado" until you put a new one in the secrets file.

### 4. Install on the VM

Connect to the VM over SSH, then follow these steps.

1. **Clone:** clone the repository over HTTPS into /opt/trending-site. The service runs from
   there, and the setup script refuses to run from anywhere else.
2. **Run the setup script:** run deploy/setup.sh with sudo. It installs Python and Caddy,
   creates the unprivileged trending user and its directories, installs the systemd service and
   timer, installs the Caddyfile, opens ports 80 and 443 in the VM firewall and enables the
   daily timer. It is safe to run again.
3. **Fill in the job secrets:** edit /etc/trending-site/env (readable only by root) and set
   GEMINI_API_KEY and GITHUB_TOKEN. GEMINI_MODEL is optional.
4. **Choose the password:** run caddy hash-password on the VM, type the password you want and
   copy the hash it prints. The password itself is never stored.
5. **Fill in the site settings:** edit /etc/caddy/site.env and set TRENDING_DOMAIN to your
   DuckDNS domain, TRENDING_USER to the login name and TRENDING_PASSWORD_HASH to the hash.
6. **Run the setup script again:** it enables and starts Caddy once the domain, the user and
   the hash are all set; until then Caddy stays off, even after a reboot, and serves nothing.
   Caddy then gets the HTTPS certificate on its own.
7. **First run (optional):** start the service trending-site with systemctl instead of waiting
   for 07:00. Then open your domain and log in.

No secret is kept in git. The keys and the password hash exist only in the two files on the VM.

## Updating

Push your changes to GitHub from your machine. Then, on the VM, pull the repository in
/opt/trending-site and run deploy/setup.sh with sudo again. The script keeps the secrets files
you already filled in.

## Where things live on the VM

| What | Where |
| --- | --- |
| Logs | The systemd journal, unit trending-site |
| State (seen repos, queue) | /var/lib/trending-site |
| Published HTML | /srv/trending-site |
| Job secrets | /etc/trending-site/env |
| Site settings and hash | /etc/caddy/site.env |

## Idle VM warning

Oracle may reclaim an Always Free VM that stays idle. Upgrading the account to Pay As You Go
avoids this. The VM stays free as long as you remain within the Always Free limits.
