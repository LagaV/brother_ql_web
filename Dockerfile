FROM python:3-alpine

COPY . /app
WORKDIR /app

RUN apk add fontconfig \
    git \
    ttf-dejavu \
    ttf-liberation \
    ttf-droid \
    font-terminus \
    font-inconsolata \
    font-dejavu \
    font-noto \
    poppler-utils && \
    fc-cache -f && \
    pip3 install -r requirements.txt

EXPOSE 8013
ENTRYPOINT [ "python3", "run.py" ]
