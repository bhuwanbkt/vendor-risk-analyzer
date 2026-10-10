# Local S3 service built from a pinned MinIO source release.
FROM golang:1.26.9-bookworm AS build

ENV CGO_ENABLED=0

RUN go install -trimpath -ldflags="-s -w" github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /go/bin/minio /usr/local/bin/minio

EXPOSE 9000 9001

ENTRYPOINT ["minio"]
CMD ["server", "/data", "--console-address", ":9001"]
