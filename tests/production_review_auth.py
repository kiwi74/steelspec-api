"""
J19 test support — REAL ES256 tokens for the production review boundary.

This module is not a test module (pytest does not collect it): it is the shared
harness the J19 tests, and the J16/J17 HTTP tests, use to present an
AUTHENTICATED request to the production routes.

WHAT IS REAL HERE
=================
Everything except one substituted stage, and the substitution is the stage no
test in this repository can perform:

  * REAL asymmetric key. A genuine NIST P-256 (ES256) key pair is generated for
    the test session with `cryptography`. Nothing is mocked about the algorithm.
  * REAL JWKS document. The private key's public half is published as a JWK with
    `alg: ES256`, `use: sig`, `kty: EC` and a `kid` — the same shape the SteelSpec
    project publishes at `/auth/v1/.well-known/jwks.json` — and verification
    selects the key by the token's own `kid` from that document.
  * REAL signature verification. `jwt.decode(..., algorithms=["ES256"])` checks
    the signature with the public key PyJWT derived from the JWKS entry. A token
    signed with any other key, or altered by a single byte, does not verify.
  * REAL claims. `iss`, `aud`, `role`, `sub` and `exp` are the claims Supabase
    Auth puts in a user access token, and the application's own rules about them
    are exercised as written.

  * SUBSTITUTED: the HTTP fetch of the JWKS endpoint. `JwksTokenVerifier` accepts
    a `key_for` callable so the key set can be supplied in-process; here it does
    the same work `PyJWKClient` does (look the token's `kid` up in the JWKS
    document and build the key from the JWK), against a document this module
    generated instead of one fetched over the network. A token whose `kid` is not
    in the document is refused, exactly as it would be in production. The
    published URL is still derived from the configured `SUPABASE_URL` by the
    production function, and a test asserts that derivation.

NOTHING HERE REACHES THE NETWORK, and no credential of the real project is read,
configured, held or printed: the keys are generated fresh, in memory, per test
session, and exist only to be verified against.
"""

from __future__ import annotations

import base64
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

from app.production_review.identity import (
    JwksTokenVerifier,
    supabase_issuer,
    supabase_jwks_url,
)

# The two claim values a Supabase Auth user token carries (see identity.py).
USER_ROLE = "authenticated"
USER_AUDIENCE = "authenticated"


def _b64(value: int, size: int) -> str:
    """One EC coordinate as an unpadded base64url JWK value."""
    return base64.urlsafe_b64encode(value.to_bytes(size, "big")).rstrip(b"=").decode("ascii")


def _jwk(public_key, *, kid: str) -> dict:
    numbers = public_key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64(numbers.x, 32),
        "y": _b64(numbers.y, 32),
        "alg": "ES256",
        "use": "sig",
        "kid": kid,
    }


class SigningKeys:
    """One generated ES256 key pair and the JWKS document that publishes it."""

    def __init__(self, kid: str = "j19-test-key"):
        self.kid = kid
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.public_key = self.private_key.public_key()
        self.jwk = _jwk(self.public_key, kid=kid)
        self.jwks = {"keys": [self.jwk]}

    def key_for(self, token: str):
        """The JWK a token names, built from the JWKS document — or a refusal.

        This is what `PyJWKClient.get_signing_key_from_jwt` does with a fetched
        document: read the token's `kid`, find that entry, build the key. An
        unknown `kid` raises, so a token signed by a key this project does not
        publish cannot be verified.
        """
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        for entry in self.jwks["keys"]:
            if entry["kid"] == kid:
                return jwt.PyJWK(entry).key
        raise jwt.InvalidKeyError(f"no key in the JWKS document has kid {kid!r}")


# The key set every test in a session verifies against, and a second, FOREIGN key
# that is never published — used to prove a token signed by a stranger is refused.
KEYS = SigningKeys()
FOREIGN = SigningKeys(kid="not-this-project")


class Tokens:
    """Tokens minted for one production application, and the verifier that reads them.

    Bound to the application's OWN configured issuer, so a token this object mints
    is a token the application accepts, and a token minted for any other issuer is
    refused.
    """

    def __init__(self, supabase_url: str):
        self.supabase_url = supabase_url
        self.issuer = supabase_issuer(supabase_url)
        self.jwks_url = supabase_jwks_url(supabase_url)
        self.keys = KEYS
        self.verifier = JwksTokenVerifier(self.jwks_url, key_for=KEYS.key_for)

    def claims(self, user_id: str = "reviewer-1", **overrides) -> dict:
        """A Supabase-shaped user token's claims, before any override."""
        claims = {
            "sub": user_id,
            "role": USER_ROLE,
            "aud": USER_AUDIENCE,
            "iss": self.issuer,
            "exp": int(time.time()) + 3600,
        }
        claims.update(overrides)
        return claims

    def token(
        self,
        user_id: str = "reviewer-1",
        *,
        key=None,
        kid: str | None = None,
        drop=(),
        **overrides,
    ) -> str:
        """An access token. `drop` removes claims (to mint a token without one) and
        `key`/`kid` sign with another key (to mint one this project did not issue)."""
        claims = self.claims(user_id, **overrides)
        for name in drop:
            claims.pop(name, None)
        signing_key = key if key is not None else self.keys.private_key
        headers = {"kid": kid if kid is not None else self.keys.kid}
        return jwt.encode(claims, signing_key, algorithm="ES256", headers=headers)

    def headers(self, user_id: str = "reviewer-1", **kwargs) -> dict:
        """The one credential the production routes read."""
        return {"Authorization": f"Bearer {self.token(user_id, **kwargs)}"}

    def foreign_token(self, user_id: str = "reviewer-1", **overrides) -> str:
        """A well-formed ES256 token signed by a key this project does not publish."""
        return self.token(
            user_id, key=FOREIGN.private_key, kid=FOREIGN.kid, **overrides,
        )


def install(monkeypatch, production) -> Tokens:
    """Points the application's token verifier at this session's test key set.

    ONE substitution, at the seam the module already exposes for it: the verifier
    the dependency asks for is the verifier that reads the generated JWKS. Every
    rule the application applies to a token — signature, algorithm, issuer,
    audience, role, subject, expiry — is the production rule, unmodified.
    """
    tokens = Tokens(production.main.SUPABASE_URL)
    monkeypatch.setattr(production.main, "identity_verifier", lambda: tokens.verifier)
    return tokens


def headers_for(project_row, **kwargs) -> dict:
    """The headers a request about `project_row` must carry to be authorized:
    a token whose subject IS that project's owner, issued by the configured issuer.

    Used by the J16/J17 HTTP tests, which drive one project at a time and must keep
    driving the same routes they always did — now with the credential every route
    requires.
    """
    import os

    return Tokens(os.environ["SUPABASE_URL"]).headers(project_row["user_id"], **kwargs)
