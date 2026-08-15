FROM python:3.11-slim
WORKDIR /app
COPY graph-context/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY graph-context/main.py ./main.py
COPY scripts/zodiac_graph.py /app/zodiac_graph.py
COPY scripts/context_engineering.py /app/context_engineering.py
EXPOSE 5070
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5070"]
