FROM otel/opentelemetry-collector-contrib:latest
COPY otel-config.yaml /etc/otel/config.yaml
EXPOSE 4317 4318 9464
CMD ["--config", "/etc/otel/config.yaml"]
