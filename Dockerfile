FROM ghcr.io/berriai/litellm:main-latest

WORKDIR /app

COPY config.yaml .

EXPOSE 4000

CMD ["litellm", "--config", "/app/config.yaml", "--port", "4000", "--num_workers", "1"]
