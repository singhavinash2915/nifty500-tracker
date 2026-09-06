# Running the pipeline on a server

The nightly job currently runs from a launchd agent on a laptop, which fires
only when the machine happens to be awake. On 4 September the 19:15 job ran at
19:29 because that is when the lid opened, and a night the laptop stays shut is
a night with no data *and no notification about its absence* — the failure
channel needs the machine as much as the job does.

A server fixes both. Everything here assumes Ubuntu; Oracle Cloud's Always Free
tier is the obvious host because it is genuinely free rather than free for a
year, but nothing below depends on the provider.

## The account is yours to create

Account creation needs identity and payment verification and agreement to
Oracle's terms, so that part cannot be delegated. Two things matter when you do
it:

**Pick an Indian region — `ap-mumbai-1` or `ap-hyderabad-1`.** The region is
fixed at signup and cannot be changed afterwards. NSE and Screener treat Indian
addresses better than American or European ones, and `preflight.sh` exists
because that is the assumption most likely to be wrong.

**Take the Ampere shape, `VM.Standard.A1.Flex.`** The free allowance is four ARM
cores and 24 GB, which is far more than this needs; one core and 6 GB is ample
and leaves room for a second instance. The x86 `E2.1.Micro` also works and is
slower. Ubuntu 22.04 or 24.04, either is fine.

## Then

```bash
# 1. Can this host reach the data at all? Five minutes here saves an hour
#    setting up a box that was never going to work.
curl -sO https://raw.githubusercontent.com/singhavinash2915/nifty500-tracker/main/scripts/cloud/preflight.sh
bash preflight.sh

# 2. Provision. Idempotent — safe to re-run.
curl -sO https://raw.githubusercontent.com/singhavinash2915/nifty500-tracker/main/scripts/cloud/setup.sh
bash setup.sh          # writes an .env template and stops

nano ~/nifty500-tracker/.env      # SUPABASE_URL and SUPABASE_SERVICE_KEY
bash setup.sh                     # again, to finish

# 3. First run by hand, because it backfills prices and takes longer than the
#    nightly twenty minutes. Watch it rather than finding out at 19:15.
sudo systemctl start n500-nightly
journalctl -u n500-nightly -f
```

The service key is in the Supabase dashboard under **Settings → API →
service_role**. It bypasses row-level security, so it belongs in `.env` on the
server (mode 600, which `setup.sh` sets) and nowhere the browser can reach.

## If preflight fails

A 403 means the host is blocked and no amount of retrying fixes it. In order of
how well they work:

- **A different region.** Indian addresses fare better with NSE.
- **Ingestion stays on the laptop**, and the box does nothing. Half the benefit,
  no risk.
- **A residential proxy.** A bigger commitment than this is worth.

Do not run `setup.sh` against a host that fails preflight. A box that runs on
schedule and fails every night is worse than a laptop that sometimes sleeps,
because it looks like it is working.

## What it installs

| | |
|---|---|
| `n500-nightly.timer` | 19:15 IST daily, `Persistent=true` |
| `n500-nightly.service` | runs `scripts/nightly.sh` |
| timezone | `Asia/Kolkata`, so the schedule reads as written |

`Persistent=true` is the line that makes this better than the laptop: if the box
was off at 19:15 the run happens as soon as it returns, rather than being
skipped in silence.

There is no desktop notification here and that is correct — the failure channel
on a server is the banner in the app, driven by the `verify` row in
`ingestion_runs`, and that reaches you wherever you are rather than only at the
machine.

```bash
systemctl list-timers n500-nightly.timer   # when it next runs
sudo systemctl start n500-nightly          # run it now
journalctl -u n500-nightly -n 50           # what happened
```

## Afterwards

Disable the laptop agent, or both will write to the same database every night —
harmless, since every job is idempotent, but it doubles the scraping and makes
the logs confusing:

```bash
launchctl unload ~/Library/LaunchAgents/com.nifty500.tracker.plist
```

Updating the code is `git -C ~/nifty500-tracker pull && bash setup.sh`; the
script re-installs dependencies and re-writes the units, so a changed schedule
or a new package needs nothing else.
