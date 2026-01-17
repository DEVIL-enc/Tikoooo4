FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements* ./

RUN if [ -f requirements.txt ]; then pip3 install --no-cache-dir -r requirements.txt; \
    elif [ -f requirements ]; then pip3 install --no-cache-dir -r requirements; \
    else pip3 install --no-cache-dir fastapi uvicorn python-multipart requests; fi

COPY . .

EXPOSE 8000

CMD ["uvicorn","main:app","--host","0.0.0.0","--port","8000"]
