.PHONY: install test eval eval-live lint web api deploy-cloud-run deploy-agent-engine tf-plan tf-apply

PROJECT ?= $(GOOGLE_CLOUD_PROJECT)
REGION  ?= us-central1

install:            ## Install runtime + dev dependencies
	pip install -r requirements.txt

lint:               ## Static checks
	ruff check .
	ruff format --check .

test:               ## Unit + orchestration tests (offline, scripted LLM)
	pytest -q

eval:               ## Offline golden-dataset regression gate (must be 100%)
	python -m eval.run_offline_eval --threshold 1.0

eval-live:          ## Live LLM eval with ADK AgentEvaluator (needs Gemini credentials)
	python -m eval.build_evalset
	adk eval dragons_den eval/dragons_den.evalset.json --config_file_path eval/test_config.json --print_detailed_results

web:                ## ADK dev UI with persistent sessions
	adk web --session_service_uri "sqlite+aiosqlite:///./.den_sessions.db" .

api:                ## Production FastAPI server locally
	python main.py

deploy-cloud-run:   ## Deploy with the ADK CLI (alternative to Terraform + CI)
	adk deploy cloud_run --project $(PROJECT) --region $(REGION) --service_name dragons-den \
	  --with_ui --trace_to_cloud dragons_den

deploy-agent-engine: ## Deploy to Vertex AI Agent Engine (managed sessions + Memory Bank)
	adk deploy agent_engine --project $(PROJECT) --region $(REGION) --display_name "Dragons Den" dragons_den

tf-plan:
	cd terraform && terraform init && terraform plan -var project_id=$(PROJECT) -var region=$(REGION)

tf-apply:
	cd terraform && terraform apply -var project_id=$(PROJECT) -var region=$(REGION)
