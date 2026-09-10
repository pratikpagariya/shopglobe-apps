"""Shared contract for all 8 services. Config + structured logging."""

from __future__ import annotations

import contextvars
import logging
import sys

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict

# Set by the request-id middleware; every log line picks it up automatically.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


class Settings(BaseSettings):
    """12-factor config. Validated at import time, so bad config fails at boot
    (loudly, in CI) rather than at 3am on the first request that touches it."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "unknown"
    env: str = "dev"
    market: str = "in"
    log_level: str = "INFO"
    port: int = 8000

    # How long to keep serving after SIGTERM before closing deps.
    # Must be < terminationGracePeriodSeconds, and covers the window where
    # EndpointSlice removal has not yet propagated to every kube-proxy.
    drain_seconds: float = 5.0

    # Dependencies. Only the ones a given service uses are ever set.
    database_url: str = ""
    redis_url: str = ""
    sqs_queue_url: str = ""
    s3_bucket: str = ""
    sns_topic_arn: str = ""
    cdn_domain: str = ""

    # Upstreams for edge-gateway (k8s DNS names in-cluster).
    auth_url: str = "http://auth-svc:8000"
    catalog_url: str = "http://catalog-svc:8000"
    cart_url: str = "http://cart-svc:8000"
    order_url: str = "http://order-svc:8000"
    notify_url: str = "http://notify-svc:8000"
    media_url: str = "http://media-svc:8000"

    upstream_timeout_seconds: float = 2.0
    upstream_retries: int = 2

    # auth-svc. In-cluster this comes from Secrets Manager via External Secrets.
    jwt_secret: str = "dev-only-change-me"
    jwt_kid: str = "k1"
    jwt_ttl_seconds: int = 900

    cache_ttl_seconds: int = 60

    aws_region: str = "ap-south-1"
    # Set to http://localstack:4566 locally; empty in AWS so boto3 uses the
    # real endpoint and Pod Identity supplies the credentials.
    aws_endpoint_url: str = ""

    @property
    def boto_kwargs(self) -> dict:
        kw: dict = {"region_name": self.aws_region}
        if self.aws_endpoint_url:
            kw["endpoint_url"] = self.aws_endpoint_url
        return kw


def _add_request_id(_, __, event_dict: dict) -> dict:
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def setup_logging(settings: Settings) -> structlog.stdlib.BoundLogger:
    """JSON logs to stdout. Labels stay low-cardinality: service, env, market,
    level. request_id goes in the line body, never in a Loki label."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=settings.log_level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_request_id,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(settings.log_level)
        ),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger().bind(
        service=settings.service_name, env=settings.env, market=settings.market
    )
