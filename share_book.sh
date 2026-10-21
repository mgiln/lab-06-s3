#!/bin/bash
set -euo pipefail

# accepting 3 position arguments: URL, bucket name, and expiration
if [[ $# -ne 3 ]]; then
    echo "Usage: $0 <book_url> <bucket_name> <expiration_seconds>" >&2
    exit 1
fi

if [[ -z "$1" || -z "$2" || -z "$3" ]]; then
    echo "Error: an argument is missing" >&2
    exit 1
fi
#variables
BOOK_URL="$1"
BUCKET="$2"
EXPIRATION="$3"

# Keep the filename from the URL and prepare the download folder.
FILENAME="$(basename "${BOOK_URL%%\?*}")"
BOOK_PATH="books/$FILENAME"
mkdir -p "books"

# Download the book; stop if the HTTP request fails.
curl --fail --location --show-error "$BOOK_URL" --output "$BOOK_PATH"

# Creating the bucket if it doesnt already exist
if aws s3 ls "s3://$BUCKET" > /dev/null 2>&1; then
    echo "Using existing bucket: $BUCKET" >&2
else
    aws s3 mb "s3://$BUCKET" --region "us-east-1"
fi

# uploading the book so it only displays in the browser
aws s3 cp "$BOOK_PATH" "s3://$BUCKET/$FILENAME" \
    --content-type "text/plain; charset=utf-8"

# presigns a URL to a book with an expiration 
aws s3 presign "s3://$BUCKET/$FILENAME" \
    --expires-in "$EXPIRATION" \
    --region "us-east-1"