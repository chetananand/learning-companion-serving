#!/usr/bin/env bash
# Run one load-generator tool as a Job in the cluster, then save the run in metrics/<run-id>/:
# the client results, the summary, the run record (run.json), and the Prometheus range queries.
# Usage (tunnel up): bash cluster/loadgen.sh <run-id> <replay|capacity|questions|tool> [args...]
#   bash cluster/loadgen.sh e3-m4-100 replay --capture /data/capture/app-calls.jsonl --mix m4 --rate 2 --duration 600
#   bash cluster/loadgen.sh e1-8k capacity --tokens 8000 --levels 1,2,4,8,16,24,32
#   bash cluster/loadgen.sh e5-2048 tool tools/pd_probe.py split --decode {decode} --prefill {prefill}
# replay, capacity, and tool get --out /data/runs/<run-id>. {decode} and {prefill} become the pod IPs,
# and {lmcache} the URL of the LMCache server on the engine node.
# LOADGEN_TIMEOUT_S (default 10800) stops the wait and the Job.
set -euo pipefail
RUN="${1:?run id}"; MOD="${2:?module}"; shift 2
cd "$(dirname "$0")/.."
# Without a kubeconfig, kubectl calls localhost:8080, which the tunnel maps to edge (G0).
export KUBECONFIG="${KUBECONFIG:-$PWD/cluster/state/kubeconfig}"
NODE2="$(cat cluster/state/node2-ip)"
LIMIT_S="${LOADGEN_TIMEOUT_S:-10800}"
pod_ip() { kubectl -n companion get pods -l "app=$1" -o jsonpath='{.items[0].status.podIP}' 2>/dev/null || true; }
D_IP="$(pod_ip vllm-decode)"; P_IP="$(pod_ip vllm-prefill)"
# The LMCache MP server listens on the node IP of the engine pods (hostNetwork, port 8080).
L_URL="http://$(kubectl -n companion get pods -l app=vllm-decode -o jsonpath='{.items[0].status.hostIP}' 2>/dev/null):8080"
ARGS=()
for a in "$@"; do a="${a//\{decode\}/$D_IP}"; a="${a//\{lmcache\}/$L_URL}"; ARGS+=("${a//\{prefill\}/$P_IP}"); done
case "$MOD" in
  replay|capacity) CMD=(python -m "app.loadgen.$MOD" ${ARGS[@]+"${ARGS[@]}"} --out "/data/runs/$RUN") ;;
  questions) CMD=(python -m app.loadgen.questions ${ARGS[@]+"${ARGS[@]}"}) ;;
  tool) CMD=(python ${ARGS[@]+"${ARGS[@]}"} --out "/data/runs/$RUN") ;;
  *) echo "unknown module $MOD (replay, capacity, questions, tool)"; exit 2 ;;
esac
CMD_JSON=$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "${CMD[@]}")
JOB="lg-${RUN//[^a-z0-9-]/-}"
# The KV event counter (evict count, handout L848) listens to every vLLM pod during the run.
EPS=$(kubectl -n companion get pods -l app.kubernetes.io/part-of=companion-vllm \
  -o jsonpath='{range .items[*]}tcp://{.status.podIP}:5556,{end}')
if [[ -n "$EPS" ]]; then
  kubectl -n companion delete job "kv-$JOB" --ignore-not-found >/dev/null
  kubectl -n companion apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata: {name: kv-$JOB, namespace: companion, labels: {app: kv-events, run: "$RUN"}}
spec:
  backoffLimit: 0
  ttlSecondsAfterFinished: 86400
  template:
    metadata: {labels: {app: kv-events}}
    spec:
      restartPolicy: Never
      terminationGracePeriodSeconds: 30
      nodeSelector: {companion.io/node: control}
      containers:
        - name: counter
          image: companion/app:dev
          imagePullPolicy: Never
          command: [python, tools/kv_events.py, --endpoints, "$EPS", --seconds, "14400", --out, /data/runs/$RUN/kv-events.json]
          volumeMounts: [{name: data, mountPath: /data}]
      volumes: [{name: data, hostPath: {path: /var/lib/companion/app-data}}]
YAML
fi
# A rerun with the same run id (session 1, E1): an old Job of this name gives its old result, or an
# "immutable field" error. So it goes first.
kubectl -n companion delete job "$JOB" --ignore-not-found --wait=true >/dev/null
kubectl -n companion apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata: {name: $JOB, namespace: companion, labels: {app: loadgen, run: "$RUN"}}
spec:
  backoffLimit: 0
  activeDeadlineSeconds: $LIMIT_S
  ttlSecondsAfterFinished: 86400
  template:
    metadata: {labels: {app: loadgen, companion.io/egress: web}}
    spec:
      restartPolicy: Never
      nodeSelector: {companion.io/node: control}
      containers:
        - name: loadgen
          image: companion/app:dev
          imagePullPolicy: Never
          command: $CMD_JSON
          envFrom: [{configMapRef: {name: companion-app}}]
          resources: {requests: {cpu: "2", memory: 2Gi}, limits: {memory: 8Gi}}
          volumeMounts: [{name: data, mountPath: /data}]
      volumes: [{name: data, hostPath: {path: /var/lib/companion/app-data}}]
YAML
# Wait for Complete or Failed. `kubectl wait` takes one condition, and a failed Job never completes.
wait_job() {
  local t=0 s
  while (( t < LIMIT_S + 60 )); do
    s=$(kubectl -n companion get job "$JOB" -o jsonpath='{.status.conditions[?(@.status=="True")].type}' 2>/dev/null || true)
    case "$s" in
      *Complete*) echo "job $JOB complete"; return 0 ;;
      *Failed*) echo "job $JOB FAILED: see metrics/$RUN/job.log"; return 1 ;;
    esac
    sleep 10; t=$((t + 10))
  done
  echo "job $JOB did not end in $LIMIT_S s"; return 1
}
START=$(date +%s)
STATUS=0; wait_job || STATUS=1
END=$(date +%s)
kubectl -n companion delete job "kv-$JOB" --wait=true --ignore-not-found >/dev/null || true  # SIGTERM: it writes
mkdir -p "metrics/$RUN"
# The run window of the Grafana images (tools/grafana_shots.py renders them after node 1 stops).
printf '{"start": %d, "end": %d}\n' "$((START - 60))" "$((END + 60))" > "metrics/$RUN/window.json"
kubectl -n companion logs "job/$JOB" > "metrics/$RUN/job.log" || true
rsync -az -e "ssh -i $HOME/.ssh/lambda_ai" "ubuntu@$NODE2:/var/lib/companion/app-data/runs/$RUN/" "metrics/$RUN/" || true
SINCE=$(python3 -c 'import datetime, sys; print(datetime.datetime.fromtimestamp(int(sys.argv[1]) - 60, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))' "$START")
# The edge access log (rule 5) and the Envoy access log, which gives the hop records (H-77).
kubectl -n companion logs -l app=edge --since-time="$SINCE" --tail=-1 > "metrics/$RUN/edge-access.jsonl" || true
kubectl -n envoy-gateway-system logs -l gateway.envoyproxy.io/owning-gateway-name=companion-gateway -c envoy \
  --since-time="$SINCE" --tail=-1 > "metrics/$RUN/envoy-access.log" || true   # VERIFY the namespace at G0
python3 tools/run_record.py "$RUN" --loadgen "$MOD ${ARGS[*]:-}" > "metrics/$RUN/run.json"
# After run.json: the hop records take the backend (the KV connector of the run) from it (fault 38).
python3 tools/hop_records.py --out "metrics/$RUN" < "metrics/$RUN/envoy-access.log" || true
python3 tools/prom_dump.py --start "$((START - 60))" --end "$((END + 60))" --out "metrics/$RUN/prom"
echo "saved metrics/$RUN"
exit "$STATUS"
