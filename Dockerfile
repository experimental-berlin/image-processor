FROM python:3.5-slim
MAINTAINER MuzHack Team <contact@muzhack.com>

WORKDIR /app
ENTRYPOINT ["./image-processor.py"]
EXPOSE 10000

# Cache dependencies in order to speed up builds
COPY requirements.txt requirements.txt
RUN pip install -U -r requirements.txt

COPY ./ .
