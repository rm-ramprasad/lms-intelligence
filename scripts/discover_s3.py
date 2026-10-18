"""S3 dataset discovery: metadata only.

    python scripts/discover_s3.py              # list CSV keys, sizes, folders
    python scripts/discover_s3.py --headers    # also print header column NAMES (first line only)

Never prints data rows. --headers reads at most the first 8 KB of each CSV, keeps only
the bytes before the first newline, and discards the rest in memory.
"""
import argparse
from collections import defaultdict

import boto3

from catalog import load_catalog

HEADER_BYTES = 8192


def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def list_csvs(s3, bucket: str, prefix: str) -> list[dict]:
    objs = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        objs += [o for o in page.get("Contents", []) if o["Key"].lower().endswith(".csv")]
    return objs


def header_names(s3, bucket: str, key: str) -> list[str]:
    body = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{HEADER_BYTES - 1}")["Body"].read()
    line = body.split(b"\n", 1)[0].rstrip(b"\r").decode("utf-8-sig", errors="replace")
    del body  # never retain data bytes beyond the header
    return [c.strip().strip('"') for c in line.split(",")]


def main() -> None:
    aws = load_catalog()["aws"]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", default=aws["profile"])
    ap.add_argument("--bucket", default=aws["source_bucket"])
    ap.add_argument("--prefix", default=aws["source_prefix"])
    ap.add_argument("--headers", action="store_true", help="print header column names (first line only)")
    args = ap.parse_args()

    s3 = boto3.Session(profile_name=args.profile, region_name=aws["region"]).client("s3")
    objs = list_csvs(s3, args.bucket, args.prefix)

    folders = defaultdict(list)
    for o in objs:
        folders[o["Key"].rsplit("/", 1)[0] + "/"].append(o)

    print(f"s3://{args.bucket}/{args.prefix}  ({len(objs)} CSV files, {human(sum(o['Size'] for o in objs))})")
    for folder, items in sorted(folders.items()):
        flag = "" if len(items) == 1 else f"   <-- {len(items)} files share this table location"
        print(f"\n{folder}{flag}")
        for o in items:
            print(f"  {o['Key'].rsplit('/', 1)[1]:<45} {human(o['Size']):>10}")
            if args.headers:
                cols = header_names(s3, args.bucket, o["Key"])
                print(f"    {len(cols)} columns: {', '.join(cols)}")


if __name__ == "__main__":
    main()
