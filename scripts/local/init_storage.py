"""Create the private local MinIO bucket; safe to run again after restarts."""

import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ConnectionClosedError,
    ReadTimeoutError,
)


def main():
    client = boto3.client(
        "s3",
        endpoint_url=os.environ["OBJECT_STORAGE_ENDPOINT"],
        region_name=os.environ["OBJECT_STORAGE_REGION"],
        aws_access_key_id=os.environ["OBJECT_STORAGE_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["OBJECT_STORAGE_SECRET_ACCESS_KEY"],
        config=Config(
            signature_version="s3v4",
            retries={"max_attempts": 0},
            connect_timeout=3,
            read_timeout=5,
        ),
    )
    bucket = os.environ["OBJECT_STORAGE_BUCKET"]
    for attempt in range(30):
        try:
            try:
                client.head_bucket(Bucket=bucket)
            except ClientError as exc:
                if exc.response["ResponseMetadata"]["HTTPStatusCode"] != 404:
                    raise
                client.create_bucket(Bucket=bucket)
            print("Local private storage bucket is ready.")
            return
        except (EndpointConnectionError, ConnectionClosedError, ReadTimeoutError):
            if attempt == 29:
                raise
            time.sleep(2)
        except ClientError as exc:
            if (
                exc.response["ResponseMetadata"]["HTTPStatusCode"] < 500
                or attempt == 29
            ):
                raise
            time.sleep(2)
    raise RuntimeError("Local storage did not become ready")


if __name__ == "__main__":
    main()
