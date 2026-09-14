.PHONY: install seed api web eval test demo

install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install

seed:
	cd backend && python -m app.db.seed

api:
	cd backend && uvicorn app.api.main:app --reload --port 8000

web:
	cd frontend && npm run dev

eval:
	cd backend && python eval/run_eval.py --json ../eval_results.json

test:
	cd backend && python -m pytest tests -q

demo: seed
	cd backend && python eval/run_eval.py
