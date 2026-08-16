FROM python:3.11-slim
WORKDIR /app
COPY training-gate/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY training-gate/main.py .
COPY scripts/zodiac_bank_profiles.py .
COPY training-config/bank-profiles.json /app/config/bank-profiles.json
EXPOSE 5050
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5050"]
