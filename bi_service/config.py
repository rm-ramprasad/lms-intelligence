"""Service configuration: AWS target and safety limits.

Defaults come from the `aws` block of semantic/tables.yaml (runtime_profile is the read-only
analytics-query-runtime IAM user, separate from the dev profile); environment variables
(BI_AWS_PROFILE, BI_AWS_REGION, BI_QUERY_TIMEOUT_SECONDS) override for deployment.
"""
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

SEMANTIC_DIR = Path(__file__).resolve().parents[1] / "semantic"


def resolve_profile(default: str | None) -> str | None:
    """Which AWS profile to use.

    1. BI_AWS_PROFILE if set ("" means: no profile, use the default credential chain).
    2. No profile when credentials are supplied via environment variables (e.g. Streamlit Community Cloud
       secrets, CI, an EC2/ECS role) — named profiles don't exist there.
    3. Otherwise the configured profile (local development).
    """
    if "BI_AWS_PROFILE" in os.environ:
        return os.environ["BI_AWS_PROFILE"] or None
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"):
        return None
    return default


@dataclass(frozen=True)
class ServiceConfig:
    profile: str | None
    region: str
    database: str
    workgroup: str
    timeout_seconds: float = 60.0
    poll_interval_seconds: float = 0.5
    default_limit: int = 100
    max_limit: int = 1000

    @classmethod
    def load(cls, semantic_dir: Path = SEMANTIC_DIR) -> "ServiceConfig":
        with open(semantic_dir / "tables.yaml", encoding="utf-8") as f:
            aws = yaml.safe_load(f)["aws"]
        return cls(
            profile=resolve_profile(aws.get("runtime_profile", aws["profile"])),
            region=os.environ.get("BI_AWS_REGION", aws["region"]),
            database=aws["database"],
            workgroup=aws["workgroup"],
            timeout_seconds=float(os.environ.get("BI_QUERY_TIMEOUT_SECONDS", 60)),
        )
