#!/bin/bash
#
# Run this on the new box BEFORE anything else.
#
#   bash preflight.sh
#
# The point of moving off the laptop is reliability, and there is exactly one
# way that backfires: NSE, niftyindices and Screener all serve a browser from a
# residential connection happily, and any of them may refuse a datacenter IP.
# If the box cannot reach the data, a perfectly provisioned server is worse than
# a laptop that sleeps — it will run on schedule and fail every night.
#
# So this checks reachability first. Five minutes here saves an hour of setup
# against a host that was never going to work.

set -uo pipefail

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
FAIL=0

check() {
  local label="$1" url="$2" expect="${3:-200}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' -m 30 -A "$UA" -L "$url" 2>/dev/null)"
  if [ "$code" = "$expect" ]; then
    printf '  [ ok ] %-34s %s\n' "$label" "$code"
  else
    printf '  [FAIL] %-34s %s (wanted %s)\n' "$label" "$code" "$expect"
    FAIL=$((FAIL + 1))
  fi
}

echo "=================================================================="
echo "  PREFLIGHT — can this machine reach the data?"
echo "=================================================================="
echo
echo "  public IP: $(curl -s -m 10 https://api.ipify.org || echo unknown)"

# Anything behind Cloudflare answers an unfamiliar IP with a challenge page, and
# printing that verbatim buries the checks below under a screenful of HTML. Take
# the answer only if it looks like a country name rather than a document.
COUNTRY="$(curl -s -m 10 https://ipinfo.io/country 2>/dev/null | tr -d '\r\n')"
case "$COUNTRY" in
  [A-Z][A-Z]) echo "  country:   $COUNTRY" ;;
  *)          echo "  country:   unknown" ;;
esac
echo

echo "  price and index data"
# One recent weekday's bhavcopy. A 404 here is fine on a holiday; a 403 is the
# answer that matters, because that is a block rather than a missing file.
YMD="$(date -u -d 'last friday' +%Y%m%d 2>/dev/null || date -u -v-fri +%Y%m%d)"
check "NSE bhavcopy (UDiFF)" \
  "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_${YMD}_F_0000.csv.zip"
check "NSE index archive" \
  "https://nsearchives.nseindia.com/content/indices/ind_close_all_01012024.csv"
check "niftyindices constituents" \
  "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"

echo
echo "  fundamentals (weekly, Sundays only)"
check "screener.in" "https://www.screener.in/company/HDFCBANK/consolidated/"

echo
echo "  database"
if [ -n "${SUPABASE_URL:-}" ]; then
  check "Supabase REST" "${SUPABASE_URL}/rest/v1/" 401
else
  echo "  [skip] Supabase — SUPABASE_URL not set yet, checked again by setup.sh"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "  Everything reachable. Run setup.sh."
  exit 0
fi

echo "  $FAIL source(s) unreachable."
echo
echo "  A 403 means this host is blocked, and no amount of retrying fixes that."
echo "  Options, in order of how well they work:"
echo "    - try a different region (ap-mumbai-1 or ap-hyderabad-1 sit inside"
echo "      India and are treated better by NSE than US or EU addresses)"
echo "    - keep ingestion on the laptop and use the box only for serving"
echo "    - a residential proxy, which is a bigger commitment than this is worth"
echo
echo "  Do not proceed with setup until this passes. A box that runs on schedule"
echo "  and fails every night is worse than a laptop that sometimes sleeps."
exit 1
