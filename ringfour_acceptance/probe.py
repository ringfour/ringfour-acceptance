"""Executed by the Platform CLI interpreter, under the worker's own account.

Only selected non-secret identity/target fields leave this process.
"""

import json

import httpx
from ringfour_cli.auth import TokenStore
from ringfour_cli.config import load_settings


def main():
    settings = load_settings()
    tokens = TokenStore().load(settings)
    if not tokens or not tokens.get("access_token"):
        print(
            json.dumps(
                {"error": "Log in with ringfour-cli login in this worker's shell."}
            )
        )
        return
    with httpx.Client(
        base_url=settings.api_url,
        timeout=10,
        trust_env=False,
        headers={"Authorization": "Bearer " + tokens["access_token"]},
    ) as client:

        def get(path):
            response = client.get(path)
            if response.status_code != 200:
                raise RuntimeError(
                    f"Identity/target discovery returned HTTP {response.status_code}"
                )
            return response.json()

        def page(path):
            items = []
            cursor = None
            while True:
                response = client.get(path, params={"cursor": cursor} if cursor else {})
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Target discovery returned HTTP {response.status_code}"
                    )
                data = response.json()
                items.extend(data["items"])
                cursor = data.get("next_cursor")
                if not cursor:
                    return items

        user = get("/v1/me")["user"]
        organizations = []
        for org in page("/v1/organizations"):
            # Membership responses may wrap the organization.
            org = org.get("organization", org)
            robots = page(f"/v1/organizations/{org['id']}/robots")
            organizations.append(
                {
                    "id": org["id"],
                    "name": org["name"],
                    "robots": [{"id": r["id"], "name": r["name"]} for r in robots],
                }
            )
        print(
            json.dumps(
                {
                    "user": {k: user.get(k) for k in ("id", "email", "display_name")},
                    "api_url": settings.api_url,
                    "selected_org": settings.config.get("selected_org"),
                    "organizations": organizations,
                }
            )
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - keyring backends have provider-specific failures
        # No response bodies, request headers, tracebacks or credential-store details.
        print(
            json.dumps(
                {
                    "error": f"Identity discovery unavailable ({type(error).__name__}). Check CLI login/API connectivity."
                }
            )
        )
