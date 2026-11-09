#!/usr/bin/env python3
"""AWS cost per site, from Cost Explorer grouped by the `Site` cost allocation tag.

    tools/site_costs.py [YYYY-MM] [--services] [--tag KEY]

    YYYY-MM     month to report (default: the current month, up to today)
    --services  also break each site down by AWS service
    --tag KEY   group by another tag instead of Site (e.g. Client)

The VenkoDemo SAM stacks tag every resource with Site/Client/Project
(infra/samconfig.toml); the Site tag has been active for billing since
2026-10-24, so costs before that date show up as "(no Site tag)". Shared
costs (AWS IoT Core messaging, SMS, untagged legacy buckets) can never be
split by tag and always land there too.

Uses the `aws` CLI with whatever credentials it is configured with. Each run
makes one Cost Explorer API call, which AWS bills at USD 0.01.
"""
import argparse
import calendar
import collections
import datetime as dt
import json
import subprocess
import sys


def month_range(month):
    today = dt.date.today()
    if month:
        year, mon = map(int, month.split("-"))
    else:
        year, mon = today.year, today.month
    start = dt.date(year, mon, 1)
    days_in_month = calendar.monthrange(year, mon)[1]
    month_end = start + dt.timedelta(days=days_in_month)
    end = min(month_end, today) if (year, mon) == (today.year, today.month) else month_end
    if end <= start:  # first of the month: nothing billed yet
        sys.exit(f"No cost data yet for {start:%Y-%m}.")
    return start, end, days_in_month


def fetch(start, end, tag):
    cmd = [
        "aws", "ce", "get-cost-and-usage",
        "--time-period", f"Start={start},End={end}",
        "--granularity", "MONTHLY",
        "--metrics", "UnblendedCost",
        "--group-by", f"Type=TAG,Key={tag}", "Type=DIMENSION,Key=SERVICE",
        "--output", "json",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode:
        sys.exit(out.stderr.strip())
    return json.loads(out.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("month", nargs="?", help="YYYY-MM (default: current month)")
    ap.add_argument("--services", action="store_true", help="break down by service")
    ap.add_argument("--tag", default="Site", help="cost allocation tag to group by")
    args = ap.parse_args()

    start, end, days_in_month = month_range(args.month)
    data = fetch(start, end, args.tag)

    per_site = collections.defaultdict(lambda: collections.defaultdict(float))
    unit = "USD"
    for period in data["ResultsByTime"]:
        for g in period["Groups"]:
            m = g["Metrics"]["UnblendedCost"]
            unit = m["Unit"]
            site = g["Keys"][0].split("$", 1)[1] or f"(no {args.tag} tag)"
            per_site[site][g["Keys"][1]] += float(m["Amount"])

    days = (end - start).days
    partial = days < days_in_month
    print(f"AWS cost by {args.tag}, {start} .. {end - dt.timedelta(days=1)} ({days} days, {unit})")
    hdr = f"{args.tag:<22}{'cost':>10}"
    if partial:
        hdr += f"{'month est.':>12}"
    print(hdr)
    print("-" * len(hdr))

    def total(site):
        return sum(per_site[site].values())

    untagged = f"(no {args.tag} tag)"
    sites = sorted((s for s in per_site if s != untagged), key=total, reverse=True)
    if untagged in per_site:
        sites.append(untagged)
    grand = 0.0
    for site in sites:
        t = total(site)
        grand += t
        line = f"{site:<22}{t:>10.4f}"
        if partial:
            line += f"{t / days * days_in_month:>12.2f}"
        print(line)
        if args.services:
            for svc, a in sorted(per_site[site].items(), key=lambda kv: -kv[1]):
                if a >= 0.0005:
                    print(f"    {a:>10.4f}  {svc}")
    print("-" * len(hdr))
    line = f"{'total':<22}{grand:>10.4f}"
    if partial:
        line += f"{grand / days * days_in_month:>12.2f}"
    print(line)


if __name__ == "__main__":
    main()
