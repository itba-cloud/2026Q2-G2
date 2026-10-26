"""Shared settings and clients.

Everything is read from the environment once, at import time. No credentials
live in the code: in the cloud they come from the execution role.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from functools import cache
from typing import Any

import boto3
import psycopg
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from psycopg.rows import dict_row

CONNECT_ATTEMPTS = 30
CONNECT_BACKOFF_SECONDS = 1.0

log = logging.getLogger("common")


def configure_logging() -> None:
    """Log to stdout only. Nothing on disk: instances are disposable."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(name)-6s | %(message)s",
        stream=sys.stdout,
    )
    # In a Lambda the runtime already gave the root logger its handler, so
    # basicConfig above does nothing there, level included.
    logging.getLogger().setLevel(logging.INFO)
    # The SDK logs credential discovery and every retry at INFO. Useful when
    # debugging the SDK itself, noise the rest of the time.
    for noisy in ("boto3", "botocore", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@dataclass(frozen=True)
class Settings:
    """Runtime configuration, from the environment."""

    bucket: str
    region: str
    endpoint_url: str | None
    db_instance: str
    db_host: str
    db_name: str
    db_user: str
    db_password: str
    db_secret_arn: str | None
    db_port: int | None

    @classmethod
    def from_env(cls) -> Settings:
        port = os.getenv("DB_PORT")
        return cls(
            bucket=os.getenv("S3_DATA_BUCKET", "lgdc-data"),
            region=os.getenv("AWS_REGION", "us-east-1"),
            # Empty against real AWS, where the SDK resolves the endpoint itself.
            endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
            db_instance=os.getenv("DB_INSTANCE", "lgdc-db"),
            db_host=os.getenv("DB_HOST", "floci"),
            db_name=os.getenv("DB_NAME", "lgdc"),
            db_user=os.getenv("DB_USER", "lgdc"),
            db_password=os.getenv("DB_PASSWORD", "lgdcpass"),
            # In AWS the password lives in Secrets Manager, never in the
            # environment; DB_PASSWORD is for a database at hand.
            db_secret_arn=os.getenv("DB_SECRET_ARN") or None,
            db_port=int(port) if port else None,
        )


SETTINGS = Settings.from_env()


def aws_client(service: str, settings: Settings = SETTINGS) -> Any:
    """Build an AWS client.

    Path-style addressing is required against an emulator: virtual-host style
    needs per-bucket DNS, which does not exist locally.
    """
    addressing = "path" if settings.endpoint_url else "auto"
    return boto3.client(
        service,
        region_name=settings.region,
        endpoint_url=settings.endpoint_url,
        config=Config(s3={"addressing_style": addressing}),
    )


def database_port(settings: Settings = SETTINGS) -> int:
    """Ask the RDS API for the port rather than hardcoding it."""
    if settings.db_port is not None:
        return settings.db_port
    described = aws_client("rds", settings).describe_db_instances(
        DBInstanceIdentifier=settings.db_instance)
    return int(described["DBInstances"][0]["Endpoint"]["Port"])


def database_password(settings: Settings = SETTINGS) -> str:
    """The password from the database's secret, or DB_PASSWORD without one."""
    if settings.db_secret_arn is None:
        return settings.db_password
    return _secret_password(settings.db_secret_arn, settings.region)


@cache
def _secret_password(secret_arn: str, region: str) -> str:
    # Cached: the secret does not change while the process lives, and a pool
    # opening connections should not ask Secrets Manager for each one.
    # Short timeouts: a caller like GET /api/ready has to give up on an
    # unreachable endpoint well before the Lambda's own timeout.
    client = boto3.client("secretsmanager", region_name=region, config=Config(
        connect_timeout=2, read_timeout=2, retries={"max_attempts": 2}))
    secret = client.get_secret_value(SecretId=secret_arn)
    return json.loads(secret["SecretString"])["password"]


def connect(
    settings: Settings = SETTINGS,
    attempts: int = CONNECT_ATTEMPTS,
) -> psycopg.Connection:
    """Open a database connection, retrying while the instance boots."""
    port = database_port(settings)
    password = database_password(settings)
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(
                host=settings.db_host,
                port=port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=password,
                autocommit=True,
                row_factory=dict_row,
            )
        except psycopg.OperationalError:
            if attempt == attempts:
                raise
            time.sleep(CONNECT_BACKOFF_SECONDS)
    raise AssertionError("unreachable")


__all__ = [
    "BotoCoreError",
    "ClientError",
    "SETTINGS",
    "Settings",
    "aws_client",
    "configure_logging",
    "connect",
    "database_password",
    "database_port",
]
