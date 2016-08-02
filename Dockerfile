FROM python:3.5-slim
MAINTAINER MuzHack Team <contact@muzhack.com>

WORKDIR /app
ENTRYPOINT ["./document-processor.py"]
EXPOSE 10000

RUN apt-get update && apt-get install -y build-essential libjpeg-dev zlib1g-dev libtiff-dev
RUN apt-get install -y pandoc texlive-latex-base texlive-fonts-recommended texlive-fonts-extra texlive-latex-extra

# Cache dependencies in order to speed up builds
COPY requirements.txt requirements.txt
RUN pip install -U pip
RUN pip install -U -r requirements.txt

RUN apt-get -y remove build-essential && apt-get autoremove -y && apt-get clean

COPY ./ .
