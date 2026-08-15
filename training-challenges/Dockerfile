FROM python:3.11-slim
WORKDIR /app
COPY training-challenges/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY training-challenges/main.py .
COPY training-challenges/index.html .
COPY scripts/zodiac_scenario_engine.py .
COPY training-config/scenarios.json /app/config/scenarios.json
COPY training-config/curriculum.json /app/config/curriculum.json
EXPOSE 5060
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5060"]
