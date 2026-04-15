FROM ghcr.io/hostinger/hvps-openclaw:latest

ARG FFMPEG_URL=https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz

RUN set -eux; \
    mkdir -p /tmp/ffmpeg; \
    curl -fsSL "${FFMPEG_URL}" -o /tmp/ffmpeg.tar.xz; \
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg --strip-components=1; \
    cp /tmp/ffmpeg/ffmpeg /usr/local/bin/ffmpeg; \
    cp /tmp/ffmpeg/ffprobe /usr/local/bin/ffprobe; \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe; \
    rm -rf /tmp/ffmpeg /tmp/ffmpeg.tar.xz
