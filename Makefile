# Local checks run on the laptop. GPU work needs the owner's approval for each launch (action A7).
SHELL := /bin/bash
OVERLAY ?= dev
NODE2 ?= $(shell cat cluster/state/node2-ip 2>/dev/null)
SSH := ssh -i ~/.ssh/lambda_ai -o StrictHostKeyChecking=accept-new ubuntu@$(NODE2)

.PHONY: test lint check render manifests plan images deploy smoke ingest sweep demo-check dashboards notebook

test:            ## unit, contract, and server tests
	uv run pytest -q

lint:            ## code style and ASD-STE100 writing rules
	uv run ruff check control app tools
	python3 tools/ste_lint.py docs/ README.md DESIGN.md notebook/

render:          ## router policy -> llm-d config, objectives, tenant windows
	uv run python -m control.router.render

manifests: render ## render every overlay (syntax and references)
	for o in sim dev t2 t4 one one-e9; do kubectl kustomize --load-restrictor LoadRestrictionsNone cluster/manifests/overlays/$$o > /dev/null && echo "$$o ok"; done

check: test lint manifests

plan:            ## Lambda stock and prices (read-only)
	python3 cluster/lambda/lambda_ctl.py plan

images:          ## build our images on node 2 and load them into k3s (no registry)
	rsync -az --exclude .venv --exclude docs/spec/source ./ ubuntu@$(NODE2):companion/ -e "ssh -i ~/.ssh/lambda_ai"
	$(SSH) 'cd companion && for i in edge guard-injection guard app browser; do \
	  sudo docker build -q -t companion/$$i:dev -f images/$$i/Dockerfile . && \
	  sudo docker save companion/$$i:dev | sudo k3s ctr images import - ; done'

deploy: check images ## tests on the laptop, images on node 2, then the install script
	bash cluster/install.sh $(OVERLAY)

smoke:           ## engine smoke before we debug the gateway box (H-82)
	bash cluster/smoke/smoke.sh $(OVERLAY)

ingest:          ## run the ingest job once (Notion -> Qdrant); the SSH tunnel to 6443 must be up
	kubectl -n companion create job ingest-$$(date +%s) --from=cronjob/ingest

sweep:           ## run the freshness sweep once (20 bookmarks, batch traffic)
	kubectl -n companion create job sweep-$$(date +%s) --from=cronjob/sweep

demo-check:      ## the acceptance run J6 (D-01 to D-12) through the tunnel to the Companion API
	uv run python -m app.demo_check --api http://127.0.0.1:8000

dashboards:      ## Grafana dashboards as code -> cluster/manifests/base/monitoring/dashboards/
	python3 tools/dashboards.py

notebook:        ## write and run the Part 5 notebook from the saved runs in metrics/
	uv run python tools/make_notebook.py --execute
