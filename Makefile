.PHONY: process merge merge-partial validate test

process:
	uv run python -m meritranker_data_ingestion.cli process-pdfs

merge:
	uv run python -m meritranker_data_ingestion.cli merge-reviewed-questions

merge-partial:
	uv run python -m meritranker_data_ingestion.cli merge-reviewed-questions --allow-partial

validate:
	uv run python -m meritranker_data_ingestion.cli validate-final-question-json batch_outputs

test:
	uv run pytest -v
