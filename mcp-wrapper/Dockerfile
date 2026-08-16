FROM python:3.11-slim
WORKDIR /app
COPY mcp-wrapper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp-wrapper/server.py .
COPY scripts/zodiac_agent_security.py /app/scripts/zodiac_agent_security.py
COPY scripts/zodiac_sandbox.py /app/scripts/zodiac_sandbox.py
EXPOSE 3000
CMD ["python", "server.py"]
