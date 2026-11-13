"""Download static challenge datasets once using the documented, API-key authenticated routes."""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import httpx

from app.config import get_settings

DOWNLOADS = {
    "products.json": "/api/products/download",
    "delivery-reliability.csv": "/api/delivery-reliability/download",
    "carbon-footprint.csv": "/api/carbon-footprint/download",
}


def download(raw_dir: Path, refresh=False):
    settings = get_settings()
    if not settings.valio_api_key:
        raise ValueError("Set VALIO_API_KEY in the server .env first.")
    raw_dir.mkdir(parents=True, exist_ok=True)
    origin = urlparse(settings.valio_base_url)
    results = {}
    with httpx.Client(
        timeout=120, follow_redirects=False, headers={"X-API-Key": settings.valio_api_key}
    ) as client:
        for filename, route in DOWNLOADS.items():
            target = raw_dir / filename
            if target.exists() and not refresh:
                results[filename] = "already downloaded; use --refresh to replace"
                continue
            url = urljoin(settings.valio_base_url, route)
            for _ in range(5):
                response = client.get(url)
                if response.is_redirect:
                    next_url = urljoin(url, response.headers["location"])
                    parsed = urlparse(next_url)
                    if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                        raise ValueError(
                            "Refusing a cross-origin download redirect; API credentials stay on the supplier host."
                        )
                    url = next_url
                    continue
                if response.status_code != 200:
                    raise ValueError(f"{filename}: supplier returned HTTP {response.status_code}")
                if filename.endswith(".json"):
                    if not isinstance(json.loads(response.content), list):
                        raise ValueError("Product download was not a catalog array.")
                elif not response.text.splitlines() or "productId," not in response.text.splitlines()[0]:
                    raise ValueError(f"{filename}: unexpected CSV headers")
                target.write_bytes(response.content)
                results[filename] = f"downloaded {len(response.content):,} bytes"
                break
            else:
                raise ValueError(f"{filename}: too many redirects")
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data/raw")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(download(args.raw_dir, args.refresh), indent=2))
    except (ValueError, httpx.HTTPError, OSError):
        parser.exit(
            1,
            "Download failed. Check VALIO_API_KEY, connectivity, permissions and supplier access. No credentials are logged.\n",
        )


if __name__ == "__main__":
    main()
