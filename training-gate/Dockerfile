FROM python:3.11-slim
WORKDIR /app
COPY training-gate/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY training-gate/main.py .
COPY scripts/zodiac_bank_profiles.py .
COPY scripts/zodiac_scenario_engine.py .
COPY training-config/bank-profiles.json /app/config/bank-profiles.json
COPY training-config/scenarios.json /app/config/scenarios.json
COPY training-config/scenario-expansion.json /app/config/scenario-expansion.json
COPY training-config/scenario-expansion-2026.json /app/config/scenario-expansion-2026.json
COPY training-config/hard-gates.json /app/config/hard-gates.json
EXPOSE 5050
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5050"]
