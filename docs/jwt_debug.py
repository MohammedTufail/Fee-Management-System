"""
jwt_debug.py
------------
Standalone diagnostic for "Signature verification failed" when the token
decodes fine with verification off. Run this directly (no Functions host,
no Azure Function decorators involved) to isolate whether the problem is:

  (a) a mangled/corrupted token (whitespace, truncation from copy-paste)
  (b) genuinely being checked against the wrong signing key / JWKS source
  (c) something in PyJWKClient's caching/lookup itself

Usage:
    export AAD_TENANT_ID="e00f19f1-12c3-4491-9bf9-c879d7ade5df"
    export AAD_API_AUDIENCE="api://dc680508-4e29-4746-95e2-91d04edf7015"
    python jwt_debug.py "<paste the raw access token here, in quotes>"

Read the output top to bottom — it stops at the first real problem found.
"""
import json
import sys
import os

import jwt
import requests
from jwt import PyJWK

TENANT_ID = os.environ.get("AAD_TENANT_ID", "")
API_AUDIENCE = os.environ.get("AAD_API_AUDIENCE", "")


def main():
    if len(sys.argv) != 2:
        print("Usage: python jwt_debug.py \"<token>\"")
        sys.exit(1)

    raw = sys.argv[1]

    # --- Step 1: check for corruption / whitespace -------------------------
    stripped = raw.strip()
    segments = stripped.split(".")
    print(f"1. Raw length: {len(raw)}   Stripped length: {len(stripped)}")
    if raw != stripped:
        print("   ⚠️  Token had leading/trailing whitespace or a newline — "
              "this alone can cause 'Signature verification failed' while "
              "everything else still looks valid. Use the STRIPPED token "
              "from here on (and make sure your Functions code strips too).")
    print(f"   Segment count: {len(segments)} (a valid compact JWT has exactly 3)")
    if len(segments) != 3:
        print("   ❌ Not a well-formed JWT — this IS your bug. Re-copy the token.")
        sys.exit(1)
    token = stripped

    # --- Step 2: read the header without verifying --------------------------
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    alg = header.get("alg")
    print(f"\n2. Header: kid={kid}  alg={alg}")

    # --- Step 3: read the claims without verifying ---------------------------
    claims = jwt.decode(token, options={"verify_signature": False})
    print(f"\n3. Unverified claims: iss={claims.get('iss')}  aud={claims.get('aud')}  "
          f"exp={claims.get('exp')}  roles={claims.get('roles')}")
    if claims.get("aud") != API_AUDIENCE:
        print(f"   ⚠️  aud in token ({claims.get('aud')}) != AAD_API_AUDIENCE env var "
              f"({API_AUDIENCE}) — fix the env var, not the code.")

    # --- Step 4: fetch JWKS from BOTH candidate endpoints and compare --------
    endpoints = {
        "tenant v2.0":  f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys",
        "common v1":    "https://login.microsoftonline.com/common/discovery/keys",
        "common v2.0":  "https://login.microsoftonline.com/common/discovery/v2.0/keys",
    }
    print("\n4. Checking which JWKS endpoints contain this kid:")
    matches = {}
    for label, url in endpoints.items():
        try:
            keys = requests.get(url, timeout=10).json().get("keys", [])
            found = next((k for k in keys if k.get("kid") == kid), None)
            print(f"   {label:12s} ({url}): {'FOUND' if found else 'not found'} "
                  f"({len(keys)} keys total)")
            if found:
                matches[label] = found
        except Exception as exc:
            print(f"   {label:12s}: request failed — {exc}")

    if not matches:
        print("\n   ❌ kid not found on ANY endpoint. The token may be for a "
              "different resource (e.g. Microsoft Graph) or a different "
              "tenant than AAD_TENANT_ID.")
        sys.exit(1)

    # --- Step 5: attempt real signature verification against each match -----
    print("\n5. Attempting real signature verification against each match:")
    any_ok = False
    for label, key_data in matches.items():
        try:
            jwk = PyJWK.from_json(json.dumps(key_data))
            jwt.decode(
                token, jwk.key, algorithms=["RS256"],
                audience=API_AUDIENCE, options={"verify_iss": False},
            )
            print(f"   {label:12s}: ✅ signature verifies successfully")
            any_ok = True
        except Exception as exc:
            print(f"   {label:12s}: ❌ {exc}")

    print()
    if any_ok:
        print("RESULT: The token and at least one JWKS endpoint agree — "
              "signature verification genuinely works with this exact token. "
              "If your Function still fails, the problem is upstream of "
              "signature checking: most likely the token got mangled between "
              "here and there (re-paste it fresh into local.settings.json / "
              "your REST client, don't reuse an old copy), or PyJWKClient is "
              "serving a stale cached JWKS from an earlier failed run — "
              "restart `func start` to clear it.")
    else:
        print("RESULT: Signature verification fails even here, with a fresh "
              "token and a freshly fetched JWKS. This means either the token "
              "is truly corrupted (get a brand new one, don't reuse this "
              "one) or it wasn't actually signed by this tenant's keys at "
              "all (double check AAD_TENANT_ID and that you didn't "
              "accidentally request a token for a different resource).")


if __name__ == "__main__":
    main()
