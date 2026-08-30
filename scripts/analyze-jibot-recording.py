#!/usr/bin/env python3
import argparse
import json
from collections import defaultdict


def value_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def add_fields(target, payload):
    if not isinstance(payload, dict):
        return

    for key, value in payload.items():
        target[key].add(value_type(value))


def load_records(path):
    with open(path, "r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_number}: invalid JSONL record: {exc}") from exc


def analyze(paths):
    summary = defaultdict(
        lambda: {
            "tx_count": 0,
            "rx_count": 0,
            "tx_fields": defaultdict(set),
            "rx_fields": defaultdict(set),
        }
    )

    for path in paths:
        for record in load_records(path):
            if record.get("event") != "message":
                continue

            command = record.get("command") or "<unknown>"
            direction = record.get("direction")
            payload = record.get("payload")
            item = summary[command]

            if direction == "tx":
                item["tx_count"] += 1
                add_fields(item["tx_fields"], payload)
            elif direction == "rx":
                item["rx_count"] += 1
                add_fields(item["rx_fields"], payload)

    return summary


def format_fields(fields):
    if not fields:
        return "    - <none>"

    lines = []
    for key in sorted(fields):
        types = "|".join(sorted(fields[key]))
        lines.append(f"    - {key}: {types}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Summarize observed JIBOT command request/response fields from JSONL recordings."
    )
    parser.add_argument("recordings", nargs="+", help="JIBOT recording JSONL file(s)")
    args = parser.parse_args()

    summary = analyze(args.recordings)
    for command in sorted(summary):
        item = summary[command]
        print(f"Command: {command}")
        print(f"  tx_count: {item['tx_count']}")
        print(f"  rx_count: {item['rx_count']}")
        print("  observed_tx_fields:")
        print(format_fields(item["tx_fields"]))
        print("  observed_rx_fields:")
        print(format_fields(item["rx_fields"]))
        print()


if __name__ == "__main__":
    main()
