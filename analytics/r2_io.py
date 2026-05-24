import os
from pathlib import Path
import boto3


def _s3_client():
    endpoint = os.environ["S3_ENDPOINT_URL"]
    access_key = os.environ["S3_ACCESS_KEY_ID"]
    secret_key = os.environ["S3_SECRET_ACCESS_KEY"]
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def upload_file(local_path: Path, bucket: str, key: str):
    s3 = _s3_client()
    s3.upload_file(str(local_path), bucket, key)


def download_file(bucket: str, key: str, local_path: Path) -> bool:
    s3 = _s3_client()
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        s3.download_file(bucket, key, str(local_path))
        return True
    except Exception:
        return False


def object_exists(bucket: str, key: str) -> bool:
    s3 = _s3_client()
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except Exception:
        return False