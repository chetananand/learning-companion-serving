# ADR-001: Engine, router, and gateway

Status: accepted (2026-09-27). This version replaces the version of 2026-09-26. The debate is in `DEBATE-LOG.md`, point 2. Owners: the project owner and Claude.

## Context

- The handout permits any engine and any provider. The constraint is live engine metrics (L626).
- It permits existing gateways (L626). It asks us to own six decisions: guard, admit, place, queue (hop), declare warm, and stay or leave (L553).
- It says: do not write our own engine scheduler (L555).
- The owner asked for a production system and said that a router must decide place. The owner did not want a gateway written by hand.

## Options

| Option | For | Against |
|---|---|---|
| E1: vLLM and a gateway that we write | Full control of each decision | We write and debug a gateway and a router in one week. The owner rejected it. |
| E2: Dynamo 1.4 with vLLM workers | Production P/D, KV-aware router, KVBM | From `dynamo-frontend` 1.5.0 the endpoint picker is a Rust program with only environment settings. We cannot put our policies in it. |
| E3: Dynamo with TensorRT-LLM workers | Fast kernels | Fewer engine metrics (no waiting, running, prefix-cache hits, preemptions, or inter-token latency). |
| **E5: vLLM, the llm-d router, and the Agent Router** | Production components with plugin config for each decision. Flow control, scorers, and a P/D decider. Full `vllm:*` metrics. | New versions (router v0.11.0). A router 429 means capacity, not a tenant limit. |

## Decision

E5:

1. Engine: vLLM v0.30.0 (fallback v0.26.0, the version that llm-d tests), one prefill Deployment and one decode Deployment.
2. Router: the llm-d router (GAIE endpoint picker) v0.11.0, or v0.10 if the smoke test fails. It does flow control (admit and queue), the scheduling profiles (place), and the P/D decision (hop).
3. Proxy: the Envoy AI Gateway, now named Agent Router (v1.1), on Envoy Gateway v1.8.1. It does the tenant token limits (Redis) and the InferencePool route.
4. Our code: `edge` (guard stage 1, stay or leave, streaming, shed metrics), the `guard` config, the `warm-controller`, the planner rules, and the router config.
5. KEDA scales the prefill and decode Deployments.

## Consequences

- We do not write a gateway, a router, or a scheduler. Our policy lives in the config of these components and in three small services.
- `edge` maps a router capacity 429 (header `x-llm-d-request-dropped-reason`) to 503 with the reason. A tenant 429 from the Agent Router stays 429.
- The Agent Router fallback cannot act on a local reply of the router. Thus `edge` makes the stay-or-leave decision.
- We lose Dynamo KVBM. ADR-002 picks the KV tier.

## VERIFY at Gate G0

1. vLLM v0.30.0 with the llm-d router and the routing sidecar.
2. The install order on k3s. First: the CRDs, Envoy Gateway with the AI Gateway values, and the gateway recipe. Then: the model servers, the InferencePool, the router, and the HTTPRoute.
3. How the router treats stale pod metrics (the class7 rule "unknown is not idle").
