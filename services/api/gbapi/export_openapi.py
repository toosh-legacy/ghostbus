"""Export the OpenAPI spec to packages/schemas/openapi.json (the TS client is generated from it)."""

from __future__ import annotations

import json

from gbcore.settings import REPO_ROOT


def main() -> int:
    from gbapi.app import create_app

    spec = create_app(pipeline_mode="off").openapi()
    out = REPO_ROOT / "packages" / "schemas" / "openapi.json"
    out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    print(out, len(spec["paths"]), "paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
