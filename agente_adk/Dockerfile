FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agentes ./agentes
EXPOSE 8000
# Interface web do Google ADK: chat + rastro de cada ferramenta chamada
CMD ["adk", "web", "--host", "0.0.0.0", "--port", "8000", "/app/agentes"]
