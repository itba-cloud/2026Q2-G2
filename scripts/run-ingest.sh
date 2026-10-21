#!/usr/bin/env bash
# Runs the SEPA ingestion now, the way the daily cron does, and shows what it
# did: what the run-ingest workflow runs (`make ingest`). Needs the stack
# deployed and `make init` done: it reads the function's name from the state.
#
#   scripts/run-ingest.sh
#   SEPA_API_URL=https://<id>.trycloudflare.com scripts/run-ingest.sh
#
# With SEPA_API_URL, the function downloads from that proxy instead of the one
# Deploy configured: a new tunnel needs no new Deploy. Without it, the one
# Deploy configured, if any. Exits non-zero if the ingestion failed, with its
# message.
#
# The invocation is asynchronous, and the result comes from the function's
# log: a run can take minutes downloading, and a synchronous invoke holds a
# connection idle all that time, which a CI runner's network may drop. The
# run carries an id, and the function logs one line with it at the end.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
function_name="$(terraform -chdir="$root/infra" output -raw ingest_function_name)"
log_group="/aws/lambda/$function_name"
mkdir -p "$root/build"
result="$root/build/ingest-result.json"
rm -f "$result"

run_id="run-ingest-$(date -u +%Y%m%dT%H%M%S)-$RANDOM"
payload="$(RUN_ID="$run_id" SEPA_API_URL="${SEPA_API_URL:-}" python3 -c '
import json, os
event = {"run_id": os.environ["RUN_ID"]}
if os.environ["SEPA_API_URL"]:
    event["sepa_api_url"] = os.environ["SEPA_API_URL"]
print(json.dumps(event))')"

# A minute back, against clock skew between this machine and CloudWatch.
since=$(( ($(date +%s) - 60) * 1000 ))
aws lambda invoke --function-name "$function_name" --invocation-type Event \
  --cli-binary-format raw-in-base64-out --payload "$payload" /dev/null >/dev/null
echo "Invoked $function_name ($run_id). Downloading 330 MB through the tunnel takes a few minutes."

# The function's timeout is 15 minutes; past 16 there is no line to wait for.
deadline=$(( $(date +%s) + 16 * 60 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 10
  # JSON, not text: the CLI pages through the results, and in text it prints
  # the query once per page.
  messages="$(aws logs filter-log-events --log-group-name "$log_group" --start-time "$since" \
    --filter-pattern "\"ingestion $run_id\"" --query 'events[].message' --output json)"
  outcome="$(MESSAGES="$messages" RUN_ID="$run_id" RESULT="$result" python3 -c '
import json, os
marker = "ingestion " + os.environ["RUN_ID"] + " "
for message in json.loads(os.environ["MESSAGES"]) or []:
    status, _, rest = message.partition(marker)[2].strip().partition(": ")
    if status == "finished":
        open(os.environ["RESULT"], "w").write(rest + "\n")
        print("finished")
        break
    if status == "failed":
        print("failed: " + rest)
        break')"
  case "$outcome" in
    finished)
      echo "The ingestion finished:"
      python3 -m json.tool "$result"
      exit 0
      ;;
    failed:*)
      echo "The ingestion failed: ${outcome#failed: }" >&2
      exit 1
      ;;
  esac
done

echo "The ingestion failed: no result from $function_name in 16 minutes (a timeout?). See its log group, $log_group." >&2
exit 1
