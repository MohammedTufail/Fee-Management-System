import json
import logging
import requests
import jwt
import azure.functions as func

TENANT_ID = "e00f19f1-12c3-4491-9bf9-c879d7ade5df"

JWKS_URL = (
    f"https://login.microsoftonline.com/"
    f"{TENANT_ID}/discovery/keys"
)

bp = func.Blueprint()


@bp.route(route="auth/signature-debug", methods=["GET"])
def SignatureDebug(req: func.HttpRequest) -> func.HttpResponse:

    auth = req.headers.get("Authorization", "")

    if not auth.startswith("Bearer "):
        return func.HttpResponse(
            json.dumps({"error": "Missing Bearer token"}),
            status_code=401,
            mimetype="application/json",
        )

    token = auth.split(" ", 1)[1]

    try:
        # Read token header WITHOUT validating it.
        header = jwt.get_unverified_header(token)

        kid = header.get("kid")
        alg = header.get("alg")

        logging.info("Token kid=%s alg=%s", kid, alg)

        # Download JWKS directly.
        response = requests.get(JWKS_URL, timeout=10)
        response.raise_for_status()

        jwks = response.json()

        matching_key = None

        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                matching_key = key
                break

        if matching_key is None:
            return func.HttpResponse(
                json.dumps({
                    "error": "Signing key not found",
                    "token_kid": kid,
                    "jwks_kids": [
                        key.get("kid")
                        for key in jwks.get("keys", [])
                    ]
                }),
                status_code=500,
                mimetype="application/json",
            )

        # Convert JWK -> RSA public key
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(
            json.dumps(matching_key)
        )

        # Now perform actual signature + claim validation.
        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience="api://dc680508-4e29-4746-95e2-91d04edf7015",
            issuer=(
                "https://sts.windows.net/"
                "e00f19f1-12c3-4491-9bf9-c879d7ade5df/"
            ),
        )

        return func.HttpResponse(
            json.dumps({
                "message": "SIGNATURE VALIDATION SUCCESS",
                "kid": kid,
                "alg": alg,
                "aud": claims.get("aud"),
                "iss": claims.get("iss"),
                "oid": claims.get("oid"),
                "roles": claims.get("roles"),
                "scp": claims.get("scp"),
            }),
            status_code=200,
            mimetype="application/json",
        )

    except Exception as exc:
        logging.exception("Signature debug failed")

        return func.HttpResponse(
            json.dumps({
                "message": "SIGNATURE VALIDATION FAILED",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }),
            status_code=500,
            mimetype="application/json",
        )
