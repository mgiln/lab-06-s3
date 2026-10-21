#!/usr/bin/env python3
"""Download classic books and stage them in a public S3 bucket."""

import json
import logging
import os
import sys
import time
from urllib.parse import urlparse

import boto3
import requests
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError


# Set up logging and one shared S3 client.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

s3 = boto3.client("s3", region_name="us-east-1")


def parse_args():
    """Read the book list, index page, and bucket name from the command line."""
    if len(sys.argv) < 4:
        log.error(
            "Usage: stage_books.py <book_urls.txt> <index.html> <bucket>"
        )
        sys.exit(1)

    list_file = sys.argv[1]
    index_path = sys.argv[2]
    bucket = sys.argv[3]

    log.info("Using bucket: %s", bucket)
    return list_file, index_path, bucket


def download_books(list_file, dest_dir):
    """Download books and return their local paths, including existing files."""
    file_paths = []

    try:
        os.makedirs(dest_dir, exist_ok=True)

        with open(list_file, encoding="utf-8") as file:
            urls = [line.strip() for line in file if line.strip()]
    except OSError as e:
        log.error("Book download invalid: %s", e)
        return file_paths

    for url in urls:
        # Extract pg1342.txt, for example, from the URL.
        filename = os.path.basename(urlparse(url).path)

        if not filename:
            log.error("Invalid filename: %s", url)
            continue

        path = os.path.join(dest_dir, filename)

        # Include existing books so they can still be uploaded on a rerun.
        if os.path.exists(path):
            log.info("Already downloaded: %s", path)
            file_paths.append(path)
            continue

        try:
            log.info("Downloading: %s", url)

            response = requests.get(url, timeout=30)
            response.raise_for_status()

            with open(path, "wb") as file:
                file.write(response.content)

            file_paths.append(path)
            log.info("Saved: %s", path)

        except requests.RequestException as e:
            log.error("Error downloading %s: %s", url, e)

        except OSError as e:
            log.error("Error saving %s: %s", path, e)

            # Remove an incomplete file so the next run can try again.
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as cleanup_error:
                log.error("Could not remove %s: %s", path, cleanup_error)

        finally:
            # Pause after each attempted download.
            time.sleep(1)

    log.info("Books available: %s", len(file_paths))
    return file_paths


def create_bucket(bucket):
    """Return True if the bucket is accessible or successfully created."""
    try:
        s3.head_bucket(Bucket=bucket)
        log.info("Bucket exists: %s", bucket)
        return True

    except ClientError as e:
        error_code = str(e.response["Error"]["Code"])

        # A forbidden bucket is not necessarily a nonexistent bucket.
        if error_code not in ("404", "NoSuchBucket", "NotFound"):
            log.error("Bucket does not exist %s: %s", bucket, e)
            return False

    except BotoCoreError as e:
        log.error("Could not check bucket %s: %s", bucket, e)
        return False

    try:
        # us-east-1 does not need a LocationConstraint.
        s3.create_bucket(Bucket=bucket)
        log.info("Created bucket: %s", bucket)
        return True

    except (ClientError, BotoCoreError) as e:
        log.error("Unable to create bucket %s: %s", bucket, e)
        return False


def make_public(bucket):
    """Make the bucket's objects publicly readable."""
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": "*",
                "Action": "s3:GetObject",
                "Resource": f"arn:aws:s3:::public-szj5dq/*",
            }
        ],
    }

    try:
        # Turn off bucket-level Block Public Access first.
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": False,
                "IgnorePublicAcls": False,
                "BlockPublicPolicy": False,
                "RestrictPublicBuckets": False,
            },
        )

        # Then attach the public read policy.
        s3.put_bucket_policy(
            Bucket=bucket,
            Policy=json.dumps(policy),
        )

        log.info("Enabled public read access: %s", bucket)
        return True

    except (ClientError, BotoCoreError) as e:
        log.error("Could not make bucket public: %s", e)
        return False


def upload_files(bucket, file_paths):
    """Upload books to the bucket root and return the successful count."""
    uploaded = 0

    for path in file_paths:
        # Use pg1342.txt as the key, without the books/ folder.
        key = os.path.basename(path)

        try:
            s3.upload_file(
                path,
                bucket,
                key,
                ExtraArgs={
                    "ContentType": "text/plain; charset=utf-8"
                },
            )

            uploaded += 1
            log.info("Uploaded: %s successfully", key)

        except (
            ClientError,
            BotoCoreError,
            S3UploadFailedError,
            OSError,
        ) as e:
            log.error("Could not upload %s: %s", path, e)

    log.info("Uploaded %s of %s books", uploaded, len(file_paths))
    return uploaded


def upload_index(bucket, index_path):
    """Upload the index page as index.html."""
    try:
        s3.upload_file(
            index_path,
            bucket,
            "index.html",
            ExtraArgs={"ContentType": "text/html"},
        )

        log.info("Uploaded index.html")
        return True

    except (
        ClientError,
        BotoCoreError,
        S3UploadFailedError,
        OSError,
    ) as e:
        log.error("unable to upload index %s: %s", index_path, e)
        return False


def main():
    """Download the corpus, prepare the bucket, and upload the files."""
    list_file, index_path, bucket = parse_args()

    file_paths = download_books(list_file, "books")

    if not file_paths:
        log.error("No books are available to upload.")
        sys.exit(1)

    if not create_bucket(bucket):
        sys.exit(1)

    if not make_public(bucket):
        sys.exit(1)

    uploaded = upload_files(bucket, file_paths)
    index_uploaded = upload_index(bucket, index_path)

    public_url = f"https://{bucket}.s3.amazonaws.com/index.html"

    if uploaded == len(file_paths) and index_uploaded:
        log.info("Staging succeeded: uploaded %s books.", uploaded)
        log.info("Public index: %s", public_url)
    else:
        log.error("Staging incomplete. Review the errors above.")
        log.info("Expected public index URL: %s", public_url)
        sys.exit(1)


if __name__ == "__main__":
    main()