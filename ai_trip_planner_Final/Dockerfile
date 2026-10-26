FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
ENV PORT=8000
EXPOSE 8000
# web API by default; for the command-line tool run: docker run <image> python -m tripplanner "<brief>"
CMD ["sh", "-c", "gunicorn tripplanner.server:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 60"]
