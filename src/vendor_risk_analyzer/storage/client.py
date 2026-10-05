from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from vendor_risk_analyzer.config import get_settings


settings = get_settings()


@lru_cache
def get_storage_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.object_storage_endpoint,
        region_name=settings.object_storage_region,
        aws_access_key_id=(
            settings.object_storage_access_key_id
        ),
        aws_secret_access_key=(
            settings.object_storage_secret_access_key
        ),
    )


def generate_upload_url(
    *,
    object_key: str,
    content_type: str,
    expires_in: int = 300,
) -> str:

    client = get_storage_client()

    return client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.object_storage_bucket,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=expires_in,
    )


def get_object_metadata(
    object_key: str,
) -> dict | None:

    client = get_storage_client()

    try:
        return client.head_object(
            Bucket=settings.object_storage_bucket,
            Key=object_key,
        )

    except ClientError as exc:
        status_code = (
            exc.response
            .get("ResponseMetadata", {})
            .get("HTTPStatusCode")
        )

        if status_code == 404:
            return None

        raise

def download_object(
    object_key: str,
) -> bytes:

    client = get_storage_client()

    response = client.get_object(
        Bucket=settings.object_storage_bucket,
        Key=object_key,
    )

    body = response["Body"]

    try:
        return body.read()

    finally:
        body.close()