#!/bin/bash
#
# Provision a fresh Ubuntu box to run the nightly pipeline.
#
#   bash preflight.sh          # first, and only continue if it passes
#   bash setup.sh
#
# Idempotent: safe to re-run after a failure or to pick up a code change.
#
# What this replaces is a launchd agent on a laptop, which only fires when the
# machine happens to be awake. On 4 September the 19:15 job ran at 19:29 because
# that is when the lid opened, and a night the laptop stays shut is a night with
# no data and no notification about its absence.

set -euo pipefail

REPO_URL="https://github.com/singhavinash2915/nifty500-tracker.git"
HOME_DIR="${HOME:-/home/ubuntu}"
REPO="$HOME_DIR/nifty500-tracker"
PY=python3

say() { printf '\n=== %s\n' "$1"; }

say "packages"
sudo apt-get update -qq
# python3-venv is separate from python3 on Ubuntu and its absence produces a
# confusing failure much later, when venv creation half-succeeds.
sudo apt-get install -y -qq python3 python3-venv python3-pip git tzdata >/dev/null

say "timezone"
# The schedule is written in IST because the market closes at 15:30 IST and the
# bhavcopy is published in the early evening. Setting the box's clock rather
# than translating the times keeps the units readable and survives DST changes
# elsewhere in the world.
sudo timedatectl set-timezone Asia/Kolkata
echo "  now $(date '+%Y-%m-%d %H:%M %Z')"

say "swap"
# `compute_technicals` peaks at about 1.4GB — it holds every price row and then
# builds a frame per symbol. The Always Free ARM shape has 24GB and does not
# care; the x86 E2.1.Micro has 1GB and will be killed by the OOM reaper part way
# through, which looks like a mysterious silent failure rather than a memory
# problem. Swap is slow and that is fine: this runs once a night with nobody
# waiting, and a job that takes forty minutes beats one that dies at twenty.
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
if [ "$TOTAL_MB" -lt 4000 ] && [ ! -f /swapfile ]; then
  echo "  ${TOTAL_MB}MB of RAM — adding 4GB of swap"
  sudo fallocate -l 4G /swapfile || sudo dd if=/dev/zero of=/swapfile bs=1M count=4096
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
  # Prefer RAM until it genuinely runs out; the default of 60 starts swapping
  # far too eagerly for a workload that is one big burst.
  sudo sysctl -q vm.swappiness=10
  grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf >/dev/null
else
  echo "  ${TOTAL_MB}MB of RAM, swap $( [ -f /swapfile ] && echo present || echo 'not needed' )"
fi

say "repository"
if [ -d "$REPO/.git" ]; then
  git -C "$REPO" pull --ff-only
else
  git clone --depth 50 "$REPO_URL" "$REPO"
fi

say "virtualenv"
if [ ! -x "$REPO/.venv/bin/python" ]; then
  $PY -m venv "$REPO/.venv"
fi
"$REPO/.venv/bin/pip" install -q --upgrade pip
"$REPO/.venv/bin/pip" install -q -r "$REPO/ingestion/requirements.txt"
echo "  $("$REPO/.venv/bin/python" --version)"

say "credentials"
if [ ! -f "$REPO/.env" ]; then
  cat > "$REPO/.env" <<'ENV'
# Fill these in, then re-run setup.sh.
# The service key bypasses row-level security, so it belongs only here and
# never in anything the browser downloads.
SUPABASE_URL=
SUPABASE_SERVICE_KEY=
ENV
  chmod 600 "$REPO/.env"
  echo "  wrote a template to $REPO/.env — fill it in and re-run this script"
  exit 1
fi
chmod 600 "$REPO/.env"
if ! grep -q '^SUPABASE_SERVICE_KEY=.\+' "$REPO/.env"; then
  echo "  SUPABASE_SERVICE_KEY is empty in $REPO/.env" >&2
  exit 1
fi
echo "  present"

say "connectivity"
"$REPO/.venv/bin/python" -m n500.jobs.doctor 2>&1 | tail -20 || true

say "systemd units"
sudo tee /etc/systemd/system/n500-nightly.service >/dev/null <<UNIT
[Unit]
Description=Nifty 500 tracker — nightly pipeline
# Without this a boot-time run can start before the network is up, fail every
# fetch, and look like a source outage.
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$REPO
ExecStart=/bin/bash $REPO/scripts/nightly.sh
# The full run takes about twenty minutes; an hour is generous enough that a
# slow night is not killed and short enough that a hang does not sit forever.
TimeoutStartSec=3600
UNIT

sudo tee /etc/systemd/system/n500-nightly.timer >/dev/null <<'UNIT'
[Unit]
Description=Nifty 500 tracker — 19:15 IST daily

[Timer]
OnCalendar=*-*-* 19:15:00
# The one line that makes this better than the laptop: if the box was off at
# 19:15, run as soon as it comes back rather than skipping the day silently.
Persistent=true
# Everyone's cron fires on the minute; a small jitter is politer to NSE.
RandomizedDelaySec=300

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now n500-nightly.timer

say "done"
systemctl list-timers n500-nightly.timer --no-pager | head -3
cat <<'NEXT'

  Useful from here:
    systemctl list-timers n500-nightly.timer   when it next runs
    sudo systemctl start n500-nightly          run it now
    journalctl -u n500-nightly -n 50           what happened
    tail -f ~/nifty500-tracker/data/logs/nightly-$(date +%F).log

  The first run backfills prices and takes longer than the nightly twenty
  minutes. Start it by hand and watch, rather than finding out at 19:15.
NEXT
