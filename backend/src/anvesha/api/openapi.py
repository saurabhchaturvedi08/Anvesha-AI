"""Write the API's OpenAPI schema, for generating the web app's typed client.

    python -m anvesha.api.openapi ../packages/api-client/openapi.json

Builds the app without starting it, so no database or keys are needed.
"""

import json
import sys
from pathlib import Path
from typing import Any

from anvesha.api.app import create_app
from anvesha.config import Settings


def openapi_schema() -> dict[str, Any]:
    return create_app(Settings(_env_file=None)).openapi()


def main(argv: list[str]) -> int:
    text = json.dumps(openapi_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
