FROM python:3.11-slim
WORKDIR /app
COPY training-challenges/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY training-challenges/main.py .
COPY training-challenges/index.html .
COPY scripts/zodiac_scenario_engine.py .
COPY scripts/zodiac_bank_profiles.py .
COPY scripts/zodiac_graph.py .
COPY scripts/context_engineering.py .
COPY scripts/zodiac_bank_simulator.py .
COPY scripts/zodiac_bank_orchestrator.py .
COPY scripts/zodiac_agent_security.py .
COPY scripts/zodiac_fraud_engine.py .
COPY scripts/zodiac_telemetry.py .
COPY scripts/zodiac_privacy.py .
COPY scripts/zodiac_resilience.py .
COPY training-config/scenarios.json /app/config/scenarios.json
COPY training-config/scenario-expansion.json /app/config/scenario-expansion.json
COPY training-config/hard-gates.json /app/config/hard-gates.json
COPY training-config/bank-profiles.json /app/config/bank-profiles.json
COPY bank-data/zodiac-bank.json /app/bank-data/zodiac-bank.json
COPY bank-data/financial-operations.json /app/bank-data/financial-operations.json
COPY bank-data/workflows.json /app/bank-data/workflows.json
COPY rag-docs /app/rag-docs
COPY training-config/curriculum.json /app/config/curriculum.json
EXPOSE 5060
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5060"]
