FROM python:3.11-slim
WORKDIR /app
COPY a2a-agents/knowledge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY a2a-agents/knowledge/main.py .
COPY scripts/zodiac_agent_security.py /app/scripts/zodiac_agent_security.py
EXPOSE 5011
CMD ["python", "main.py"]
