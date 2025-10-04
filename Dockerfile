ARG TARGETPLATFORM
FROM --platform=$TARGETPLATFORM python:3-alpine

WORKDIR /app
COPY . /app

ARG TARGETARCH

RUN if [ $TARGETARCH == "arm" ]; then \
        apk update --no-cache && \
        apk add --no-cache \
        # Build dependencies for Pillow
        gcc \
        musl-dev \
        zlib-dev \
        jpeg-dev \
        tiff-dev \
        freetype-dev \
        lcms2-dev \
        libwebp-dev \
        tcl-dev \
        tk-dev \
        harfbuzz-dev \
        fribidi-dev \
        libimagequant-dev \
        libxcb-dev \
        openjpeg-dev \
    ; fi

RUN apk update --no-cache && \
    apk add --no-cache \
    fontconfig \
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

RUN if [ $TARGETARCH == "arm" ]; then \
        # Clean up build dependencies to reduce image size
        apk del gcc musl-dev \
    ; fi

EXPOSE 8013

# Create volume for persistent data (printers.json, etc.)
VOLUME /app/instance

ENTRYPOINT ["python3", "run.py"]