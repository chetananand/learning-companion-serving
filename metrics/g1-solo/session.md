# G1 solo session log (node 1, 2 x H100, us-southeast-1)

- 02:46 UTC (19:46 PDT): launch, instance 8946a74e, active at 02:47.
- 02:48 UTC: SSH works, 2 x H100 80GB HBM3.
- 02:49 UTC: k3s server Ready (23 s). Engine objects applied (overlay t2).
- 02:50 UTC: FAULT 1. The LMCache server bound 127.0.0.1:5555, so the vLLM pods could not reach tcp://HOST_IP:5555.
  Fix: `--host $(HOST_IP) --http-host $(HOST_IP)` (lmcache-server.yaml). It now listens on 172.26.132.208.
- 02:51 UTC: FAULT 2. Both vLLM pods crashed: no DNS ("Temporary failure in name resolution" for huggingface.co).
  CoreDNS was not ready: the kubernetes Service pointed at the public IP (from --node-external-ip), and the
  Lambda firewall blocks it. Fix: advertise-address = the private IP (k3s-server.sh, and /etc/rancher/k3s/config.yaml on this node).
- 02:54 UTC: CoreDNS Ready. The vLLM pods resolve the model and load it.
- 02:55 UTC: FAULT 3. The decode pod refused its config: "max_tokens_per_mm_item (2496) is larger than
  max_num_batched_tokens (2048)" (one Gemma 4 image item in one step). Fix: decode --max-num-batched-tokens=2560.
  E5 now compares 2560 with 4096.
- 02:57 UTC: both engines built their KV cache (E1 data):
  prefill: 36.23 GiB, 43,094 tokens, 1.32x at 32,768 tokens. Decode: 38.36 GiB, 173,701 tokens, 5.30x at 32,768.
  The design assumed about 20 decode sequences at 24K tokens. Measured: about 7.
- 03:00 UTC: FAULT 4. Both pods waited on the LMCache registration. The server failed: "CUDA error: mapping of
  buffer object failed" (torch _share_cuda_ needs one /dev/shm for all processes, but each vLLM pod has its own).
  Fix: LMCache isolated IPC (server --isolated-ipc, connector lmcache.mp.isolated_ipc true on both pods).
- 03:04 UTC: all 3 pods Ready (isolated IPC works). G1 'P/D startup with MultiConnector and HMA' passes. Step 5 starts.
- 03:05 UTC: tool-call suite 31/40. All 9 misses: the case builder gave evidence about another topic, and the
  model searched again (correct behavior). tools/make_g1_cases.py now gives matching evidence. Not run again.
- 03:08 UTC: STOP. The owner asked how we capture the Grafana dashboards. There was no plan, so G1 stopped here.
  Saved: metrics/g1-solo-20260928T030833Z. Node 1 terminated at 03:08:46 UTC (22 minutes, about 3.07 USD).
