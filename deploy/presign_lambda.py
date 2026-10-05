"""Presign endpoint for nameplate photo uploads (HomeKeeper cold path).

GET ?token=<upload token>
  -> 200 {"upload_url": "<S3 presigned PUT url>", "s3_key": "uploads/<token>.jpg"}
  -> 400 {"error": "missing token"}
  -> 403 {"error": "invalid or expired token"}

The UPLOAD#<token> row (pk USER#<demo user>) is the auth: it is created by
the hot path's start_photo_onboarding tool and expires after 15 minutes.
No Content-Type is signed into the presigned URL so phone cameras can upload
JPEG/PNG/HEIC without a signature mismatch (the key name is cosmetic).
"""

import json
import os
import time

import boto3

TABLE = os.environ["HOMEKEEPER_TABLE"]
BUCKET = os.environ["UPLOADS_BUCKET"]
USER_ID = os.environ.get("DEMO_USER_ID", "demo-household-1")
REGION = os.environ.get("AWS_REGION", "us-east-1")

_ddb = None
_s3_client = None


def _ddb_table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=REGION).Table(TABLE)
    return _ddb


def _s3():
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=REGION)
    return _s3_client


def _resp(status, obj):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(obj),
    }


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    token = (params.get("token") or "").strip()
    if not token:
        return _resp(400, {"error": "missing token"})
    try:
        item = (
            _ddb_table()
            .get_item(Key={"pk": f"USER#{USER_ID}", "sk": f"UPLOAD#{token}"})
            .get("Item")
        )
    except Exception as e:  # pragma: no cover - defensive
        return _resp(500, {"error": f"dynamodb error: {e}"})
    now = int(time.time())
    if not item or item.get("entity") != "UPLOAD" or int(item.get("ttl", 0)) <= now:
        return _resp(403, {"error": "invalid or expired token"})
    key = f"uploads/{token}.jpg"
    url = _s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=900,
    )
    return _resp(200, {"upload_url": url, "s3_key": key})
