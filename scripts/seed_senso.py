#!/usr/bin/env python3
"""Seed RELAY's fictional Senso knowledge base and wait for processing."""
import json
import pathlib

from paypilot.integrations import SensoClient


ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ROOT / "examples" / "senso_kb"


def main():
    client = SensoClient()
    documents = [{"title": p.name, "text": p.read_text(encoding="utf-8")}
                 for p in sorted(DOCS.glob("*.md"))]
    print(json.dumps(client.seed_documents(documents), indent=2))


if __name__ == "__main__":
    main()
