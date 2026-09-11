"""
auth.py
-------
Validates Azure AD (Microsoft Entra ID) issued JWT bearer tokens and enforces
role-based access control (RBAC) inside the Azure Function itself.

Defense-in-depth: primary AAD token validation also happens at the API
Management gateway (see apim/apim-policy.xml) via the `validate-jwt` policy.
Re-checking here means the Function is still safe even if called directly.

Setup required in Azure AD App Registration:
  1. Expose an API (Application ID URI, e.g. api://fee-management-api)
  2. Add App Roles: "Student" and "Admin"
     (App registration -> App roles -> Create app role)
  3. Assign users/groups to those roles under Enterprise Applications
  4. Tokens requested for this API will then contain a "roles": [...] claim

----------------------------------------------------------------------------
NOTE on v1.0 vs v2.0 tokens (fixed after a real "Signature verification
failed" bug hit during Part 2 testing):

Azure AD can hand back either flavour of access token for the SAME API,
depending on how it was requested:
  - v1.0 token: iss = https://sts.windows.net/{tenant}/
    (e.g. `az account get-access-token --resource <app-id-uri>`)
  - v2.0 token: iss = https://login.microsoftonline.com/{tenant}/v2.0
    (e.g. `az account get-access-token --scope <app-id-uri>/.default`,
     or when the App Registration manifest sets
     "accessTokenAcceptedVersion": 2)

Both are signed with the SAME underlying Microsoft signing keys, and
Microsoft's own troubleshooting docs confirm the right JWKS source is:
  - v1.0 tokens -> https://login.microsoftonline.com/common/discovery/keys
  - v2.0 tokens -> https://login.microsoftonline.com/common/discovery/v2.0/keys
    (the TENANT-specific .../{tenant}/discovery/v2.0/keys endpoint also
    works and is what Microsoft's own ASP.NET samples use)

The actual bug we hit was validating a v1.0 token against a hardcoded
v2.0-only ISSUER string, which fails validation (though NOT with a
"signature" error — issuer mismatch throws a different, later error).
If you see "Signature verification failed" specifically (not "invalid
issuer"), and the token decodes fine with verification OFF, the signature
check itself is failing, which points to a genuinely different key being
used than the one that signed the token. In practice this is very often
NOT a code bug at all but a mangled token: a trailing newline, stray
whitespace, or a missing/extra character from copy-pasting a long JWT out
of a terminal into a REST client. See the `.strip()` below and the
diagnostic script in docs/jwt_debug.py.

To be robust either way, this module:
  1. Fetches JWKS from the tenant-specific v2.0 discovery endpoint (works
     for both v1.0 and v2.0 tokens issued by that tenant).
  2. Accepts EITHER issuer format instead of hardcoding one.
  3. Strips stray whitespace/newlines from the raw token before decoding.
----------------------------------------------------------------------------
"""

import os
import json
import logging
from functools import wraps

import jwt
from jwt import PyJWKClient
import azure.functions as func

TENANT_ID = os.environ.get("AAD_TENANT_ID", "")
API_AUDIENCE = os.environ.get("AAD_API_AUDIENCE", "")  # Application ID URI or client ID

# Tenant-specific v2.0 discovery endpoint. Confirmed by Microsoft's own
# troubleshooting docs and sample code to serve valid signing keys for
# BOTH v1.0 and v2.0 tokens issued by this tenant.
JWKS_URL = f"https://login.microsoftonline.com/{TENANT_ID}/discovery/v2.0/keys"

# Accept whichever issuer format the token actually has — don't hardcode
# v1 vs v2 since Azure AD can issue either for the same API/app registration.
VALID_ISSUERS = {
    f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",  # v2.0 tokens
    f"https://sts.windows.net/{TENANT_ID}/",                 # v1.0 tokens
}

_jwks_client = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(JWKS_URL)
    return _jwks_client


class AuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code


def validate_token(auth_header: str) -> dict:
    """Validates the Bearer token and returns the decoded claims."""
    if not auth_header or not auth_header.startswith("Bearer "):
        raise AuthError("Missing or malformed Authorization header")

    # Defensive: strip whitespace/newlines that can sneak in from copy-pasting
    # a long token out of a terminal into a REST client. A token with an
    # extra trailing character/space still "looks" like a JWT (3 segments)
    # and will decode fine with verification off, but fails the signature
    # check — which looks identical to a genuine key mismatch.
    token = auth_header.split(" ", 1)[1].strip()

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=API_AUDIENCE,
            options={"verify_iss": False},  # verified manually below (v1 vs v2 issuer)
        )
    except jwt.PyJWTError as exc:
        logging.warning("Token validation failed: %s", exc)
        raise AuthError(f"Invalid token: {exc}")

    if claims.get("iss") not in VALID_ISSUERS:
        logging.warning("Token validation failed: unexpected issuer %r", claims.get("iss"))
        raise AuthError("Invalid token: unexpected issuer")

    return claims


def get_roles(claims: dict) -> list:
    return claims.get("roles", [])


def require_role(*allowed_roles):
    """
    Decorator for Azure Function HTTP triggers.
    Validates the token and ensures the caller has one of the allowed roles.

    Claims are attached to the request as `req._claims` rather than passed
    as an extra function kwarg — the Azure Functions Python v2 host binds
    trigger parameters by inspecting the route function's declared
    signature, and a bare HTTP-triggered function only declares `req`.
    Handlers read `claims = req._claims` themselves (see PaymentStatus,
    ListStudents, UpdateFeeRecord for the pattern).
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(req: func.HttpRequest, *args, **kwargs):
            try:
                claims = validate_token(req.headers.get("Authorization"))
                roles = get_roles(claims)
                if not any(r in roles for r in allowed_roles):
                    return func.HttpResponse(
                        json.dumps({"error": "Forbidden: insufficient role"}),
                        status_code=403,
                        mimetype="application/json",
                    )
                req._claims = claims
            except AuthError as exc:
                return func.HttpResponse(
                    json.dumps({"error": exc.message}),
                    status_code=exc.status_code,
                    mimetype="application/json",
                )
            return fn(req, *args, **kwargs)

        return wrapper

    return decorator
