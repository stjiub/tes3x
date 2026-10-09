# The multiplayer server alone: tes3x.net, the modules it imports and its default start
# points. It needs only the standard library and cryptography.
FROM python:3.12-slim

RUN pip install --no-cache-dir "cryptography>=43,<47"
WORKDIR /tes3x
COPY src/tes3x/*.py src/tes3x/
COPY examples/starts.toml examples/
RUN useradd --system --uid 10001 tes3x && mkdir /world && chown tes3x /world
USER tes3x
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/tes3x/src

# As Ctrl+C does, SIGINT asks every joined console to save before the server exits.
STOPSIGNAL SIGINT
EXPOSE 26500/udp
VOLUME /world
ENTRYPOINT ["python", "-m", "tes3x", "net", "serve", "--world", "/world"]
