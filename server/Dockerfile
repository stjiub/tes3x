# TES3X's servers: multiplayer (`net serve`) and content (`content serve`), each started by its
# Compose service's command. They need only the standard library and cryptography.
FROM python:3.12-slim

RUN pip install --no-cache-dir "cryptography>=43,<47"
WORKDIR /tes3x
COPY src/tes3x/ src/tes3x/
COPY examples/starts.toml examples/
RUN useradd --system --uid 10001 tes3x && mkdir /world /state \
    && chown tes3x /world /state
USER tes3x
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/tes3x/src

# As Ctrl+C does: the multiplayer server asks every joined console to save before it exits.
STOPSIGNAL SIGINT
EXPOSE 26500/udp 26510/tcp
VOLUME /world /state
ENTRYPOINT ["python", "-m", "tes3x"]
CMD ["net", "serve", "--world", "/world"]
