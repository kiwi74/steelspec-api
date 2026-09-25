"""
Milestone J19 — WHO THE REVIEWER IS.

A production review request carries one credential: the caller's Supabase Auth
access token, in an `Authorization: Bearer <token>` header. This module is the
whole of what this application does with it, and it is deliberately the
smallest thing that can be done:

    the token  ->  the issuer's OWN public key  ->  the claims  ->  WHO

WHAT THIS MODULE IS NOT
=======================
  * NOT a second authentication system. The issuer is Supabase Auth — the same
    authority the SteelSpec frontend authenticates against, and the same one the
    database's own row-level-security policies read `auth.uid()` from. Nothing
    here mints, stores, rotates or looks up a credential.
  * NOT a credential holder. Verification uses the project's PUBLIC JSON Web
    Key Set, which this project publishes because it signs asymmetrically
    (ES256). No shared secret, no signing key and no service key is read,
    configured or held by this module — so there is no new secret to leak and
    none to rotate.
  * NOT a role system. The claim vocabulary below is Supabase's own
    (`iss`, `aud`, `role`, `sub`, `exp`); no role, scope or permission is
    invented here, because none exists in the database to reuse.

WHY THE RULES ARE STATED HERE AND NOT LEFT TO THE JWT LIBRARY
=============================================================
The library is authoritative for exactly one thing: whether the signature is
genuine and whether the token's own time window has passed. Everything else —
whose token this is supposed to be, who it must have been issued to, what kind
of token it must be, and that it must name a subject — is THIS application's
rule, stated once, in one place, in plain code that a test can drive directly.
`verify_access_token` refuses on every one of them, and each refusal has its own
deterministic code so a reader can see WHICH rule refused rather than a single
opaque "not authenticated".

FAIL-CLOSED
===========
There is no path in this module that returns an identity it did not fully
establish. An absent header, a malformed header, a token the verifier cannot
verify, a foreign issuer, a foreign audience, a non-user token and a token with
no subject all raise. `ReviewerIdentity` cannot be constructed from a partial
verification, because `verify_access_token` is the only function here that
constructs one.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

__all__ = [
    "ALGORITHMS",
    "AUDIENCE_AUTHENTICATED",
    "AUTHORIZATION_SCHEME",
    "IDENTITY_REFUSALS",
    "IDENTITY_REFUSAL_MALFORMED_TOKEN",
    "IDENTITY_REFUSAL_NO_EXPIRY",
    "IDENTITY_REFUSAL_NO_SUBJECT",
    "IDENTITY_REFUSAL_NO_TOKEN",
    "IDENTITY_REFUSAL_NOT_A_USER_TOKEN",
    "IDENTITY_REFUSAL_UNVERIFIED",
    "IDENTITY_REFUSAL_WRONG_AUDIENCE",
    "IDENTITY_REFUSAL_WRONG_ISSUER",
    "ROLE_AUTHENTICATED",
    "IdentityRefused",
    "JwksTokenVerifier",
    "ReviewerIdentity",
    "bearer_token",
    "reviewer_from_authorization",
    "supabase_issuer",
    "supabase_jwks_url",
    "verify_access_token",
]

# --------------------------------------------------------------------------------------
# The vocabulary — Supabase Auth's own, read off its tokens rather than invented.
# --------------------------------------------------------------------------------------
AUTHORIZATION_SCHEME = "Bearer"

# The audience and role of a token minted for an end user. A Supabase project
# issues user tokens with `aud = "authenticated"` and `role = "authenticated"`;
# an anonymous or administrative token names a different role, and this
# application accepts only a user token.
AUDIENCE_AUTHENTICATED = "authenticated"
ROLE_AUTHENTICATED = "authenticated"

# The ONLY algorithms this application will verify. Restricting the list is a
# security property, not a preference: an unrestricted verifier can be fooled by
# a token that claims a symmetric algorithm and is "signed" with the public key
# as the HMAC secret — the classic algorithm-confusion forgery. ES256 is the
# algorithm this project's published keys actually carry (JWKS `alg`), so a token
# that says anything else is not one this issuer issued.
ALGORITHMS = ("ES256",)

IDENTITY_REFUSAL_NO_TOKEN = "IDENTITY_NO_TOKEN"
IDENTITY_REFUSAL_MALFORMED_TOKEN = "IDENTITY_MALFORMED_TOKEN"
IDENTITY_REFUSAL_UNVERIFIED = "IDENTITY_UNVERIFIED"
IDENTITY_REFUSAL_WRONG_ISSUER = "IDENTITY_WRONG_ISSUER"
IDENTITY_REFUSAL_WRONG_AUDIENCE = "IDENTITY_WRONG_AUDIENCE"
IDENTITY_REFUSAL_NOT_A_USER_TOKEN = "IDENTITY_NOT_A_USER_TOKEN"
IDENTITY_REFUSAL_NO_SUBJECT = "IDENTITY_NO_SUBJECT"
IDENTITY_REFUSAL_NO_EXPIRY = "IDENTITY_NO_EXPIRY"

IDENTITY_REFUSALS = (
    IDENTITY_REFUSAL_NO_TOKEN,
    IDENTITY_REFUSAL_MALFORMED_TOKEN,
    IDENTITY_REFUSAL_UNVERIFIED,
    IDENTITY_REFUSAL_WRONG_ISSUER,
    IDENTITY_REFUSAL_WRONG_AUDIENCE,
    IDENTITY_REFUSAL_NOT_A_USER_TOKEN,
    IDENTITY_REFUSAL_NO_SUBJECT,
    IDENTITY_REFUSAL_NO_EXPIRY,
)


class IdentityRefused(ValueError):
    """The caller's identity could not be established, for the stated reason.

    `code` is one of `IDENTITY_REFUSALS` and `detail` is a human-readable reason.
    Both are safe to show a caller: neither carries the token, any part of it, or
    any claim the token happened to contain. Failure detail is about the RULE
    that refused, never about the credential that was presented.
    """

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ReviewerIdentity:
    """The authenticated reviewer: the four facts that were established, and
    nothing else.

    It carries no token, no claims mapping and no raw credential — only the
    subject the issuer vouched for, the role it was issued with, the issuer that
    issued it, and when it stops being valid. An identity is only ever produced
    by `verify_access_token`.
    """

    user_id: str
    role: str
    issuer: str
    expires_at: int
    email: str | None = None


# --------------------------------------------------------------------------------------
# Where a Supabase project publishes its identity endpoints — derived from the
# one URL this application already configures, never hand-typed.
# --------------------------------------------------------------------------------------
def _endpoint(supabase_url: str, path: str) -> str:
    if not (isinstance(supabase_url, str) and supabase_url.strip()):
        raise ValueError("supabase_url must be a non-empty str.")
    return supabase_url.strip().rstrip("/") + path


def supabase_issuer(supabase_url: str) -> str:
    """The `iss` every access token of this project carries."""
    return _endpoint(supabase_url, "/auth/v1")


def supabase_jwks_url(supabase_url: str) -> str:
    """Where this project publishes the PUBLIC keys its tokens are signed with."""
    return _endpoint(supabase_url, "/auth/v1/.well-known/jwks.json")


# --------------------------------------------------------------------------------------
# The header, read strictly.
# --------------------------------------------------------------------------------------
def bearer_token(authorization: str | None) -> str | None:
    """The token of an `Authorization: Bearer <token>` header, or None.

    Strict on purpose, because every lenient reading of this header is a way to
    smuggle a second credential or a second identity past a caller that only
    looked at the first one. The header must be exactly one scheme and one token,
    separated by whitespace; the scheme must be `Bearer` (case-insensitively, as
    HTTP specifies); anything else — no header, an empty header, a bare token
    with no scheme, `Basic ...`, two tokens, a token containing whitespace or a
    comma — yields None, which `verify_access_token` refuses.

    A cookie, a query parameter, a form field and a session variable are
    deliberately NOT read here: this function takes the header's value and
    nothing else, so no other input can establish an identity.
    """
    if not isinstance(authorization, str):
        return None
    parts = authorization.split()
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != AUTHORIZATION_SCHEME.lower():
        return None
    if not token or "," in token:
        return None
    return token


# --------------------------------------------------------------------------------------
# The verifier — signature and time window, from the issuer's public keys.
# --------------------------------------------------------------------------------------
class JwksTokenVerifier:
    """Verifies a Supabase Auth access token against the project's PUBLIC JWKS.

    The only credential involved is a public key, fetched from the endpoint the
    issuer publishes it at; no shared secret is configured, read or held. A token
    whose signing key this verifier cannot find is refused rather than accepted
    unverified, and the `require=["exp"]` option means a token that states no
    expiry is refused too — a credential that never expires is not one this
    application will act on.

    `key_for` exists so that a test can supply the public key of a key pair it
    generated itself, and so the verification RULES this class applies (the
    algorithm restriction, the required expiry, the signature check) are
    exercised for real rather than bypassed. When it is not supplied, the
    project's published JWKS is used.
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        key_for: Callable[[str], object] | None = None,
        algorithms: tuple[str, ...] = ALGORITHMS,
    ):
        if not (isinstance(jwks_url, str) and jwks_url.strip()):
            raise ValueError("jwks_url must be a non-empty str.")
        if key_for is not None and not callable(key_for):
            raise TypeError("key_for must be callable or None.")
        algorithms = tuple(algorithms)
        if not algorithms:
            raise ValueError("algorithms must name at least one JWT algorithm.")
        for algorithm in algorithms:
            # Refused here rather than hoped away: `none` accepts an unsigned
            # token, and an HMAC algorithm makes an asymmetric verifier forgeable
            # with its own public key.
            if algorithm.lower() == "none" or algorithm.upper().startswith("HS"):
                raise ValueError(
                    f"{algorithm!r} is not an acceptable token algorithm: this verifier "
                    "accepts only the asymmetric algorithm the issuer signs with."
                )
        self._jwks_url = jwks_url.strip()
        self._key_for = key_for
        self._algorithms = algorithms
        self._client = None

    @property
    def jwks_url(self) -> str:
        return self._jwks_url

    @property
    def algorithms(self) -> tuple[str, ...]:
        return self._algorithms

    def signing_key(self, token: str):
        """The public key this token claims to be signed with."""
        if self._key_for is not None:
            return self._key_for(token)
        import jwt  # imported here: only a process that verifies tokens loads this

        if self._client is None:
            self._client = jwt.PyJWKClient(self._jwks_url)
        return self._client.get_signing_key_from_jwt(token).key

    def __call__(self, token: str) -> Mapping:
        """The token's claims, or an exception. Never a partially-checked token.

        The audience is deliberately NOT checked here: it is this module's own
        rule, stated once in `verify_access_token`, so it cannot be enforced
        twice with two different values.
        """
        import jwt

        return jwt.decode(
            token,
            self.signing_key(token),
            algorithms=list(self._algorithms),
            options={"verify_aud": False, "require": ["exp"]},
        )


# --------------------------------------------------------------------------------------
# The rule — this application's own, stated once.
# --------------------------------------------------------------------------------------
def verify_access_token(
    token: str | None,
    *,
    verifier: Callable[[str], Mapping],
    issuer: str,
    audience: str = AUDIENCE_AUTHENTICATED,
) -> ReviewerIdentity:
    """The reviewer this token establishes, or `IdentityRefused`.

    Every rule is checked, in order, and each failure names itself:

      1. a token was presented at all;
      2. the verifier verified it — its signature is genuine and its own time
         window has not passed;
      3. it was issued by THIS project's auth service (`iss`);
      4. it was issued FOR an end user of this application (`aud`);
      5. it is a USER token, not an anonymous or administrative one (`role`);
      6. it names a subject (`sub`) — an identity with no subject is nobody;
      7. it states an expiry (`exp`) — a credential that never expires is not a
         credential this application will act on.

    Nothing here interprets the token's other claims, and nothing here stores
    them: whatever else a valid token carries stays in the token.
    """
    if not callable(verifier):
        raise TypeError(
            "verifier must be callable: an identity is only ever established by verifying "
            "the token the caller presented."
        )
    if not isinstance(token, str) or not token.strip():
        raise IdentityRefused(
            IDENTITY_REFUSAL_NO_TOKEN,
            "no access token was presented, so no reviewer was identified.",
        )
    if token != token.strip() or any(character.isspace() for character in token):
        # `bearer_token` already enforces this; restated because this function is
        # also reachable directly, and a token with whitespace in it is two
        # credentials wearing one header.
        raise IdentityRefused(
            IDENTITY_REFUSAL_MALFORMED_TOKEN,
            "the access token is not a single well-formed token.",
        )
    try:
        claims = verifier(token)
    except IdentityRefused:
        raise
    except Exception as error:
        # The verifier's own exception text can name the failure (expired,
        # bad signature, unknown key). It is reported by TYPE, and the token
        # itself is never echoed.
        raise IdentityRefused(
            IDENTITY_REFUSAL_UNVERIFIED,
            f"the access token could not be verified ({type(error).__name__}), so no "
            "reviewer was identified.",
        ) from error
    if not isinstance(claims, Mapping):
        raise IdentityRefused(
            IDENTITY_REFUSAL_UNVERIFIED,
            f"the access token verified to {type(claims).__name__} rather than a set of "
            "claims, so no reviewer was identified.",
        )
    if claims.get("iss") != issuer:
        raise IdentityRefused(
            IDENTITY_REFUSAL_WRONG_ISSUER,
            "the access token was not issued by this project's auth service.",
        )
    if claims.get("aud") != audience:
        raise IdentityRefused(
            IDENTITY_REFUSAL_WRONG_AUDIENCE,
            "the access token was not issued for an end user of this application.",
        )
    if claims.get("role") != ROLE_AUTHENTICATED:
        raise IdentityRefused(
            IDENTITY_REFUSAL_NOT_A_USER_TOKEN,
            "the access token is not a user token, so it identifies no reviewer.",
        )
    subject = claims.get("sub")
    if not (isinstance(subject, str) and subject.strip()):
        raise IdentityRefused(
            IDENTITY_REFUSAL_NO_SUBJECT,
            "the access token names no subject, so it identifies no reviewer.",
        )
    expires_at = claims.get("exp")
    if isinstance(expires_at, bool) or not isinstance(expires_at, int):
        raise IdentityRefused(
            IDENTITY_REFUSAL_NO_EXPIRY,
            "the access token states no expiry, so it is not a credential this application "
            "will act on.",
        )
    email = claims.get("email")
    return ReviewerIdentity(
        user_id=subject.strip(),
        role=ROLE_AUTHENTICATED,
        issuer=issuer,
        expires_at=expires_at,
        email=email if isinstance(email, str) and email.strip() else None,
    )


def reviewer_from_authorization(
    authorization: str | None,
    *,
    verifier: Callable[[str], Mapping],
    issuer: str,
    audience: str = AUDIENCE_AUTHENTICATED,
) -> ReviewerIdentity:
    """The reviewer an `Authorization` header establishes, or `IdentityRefused`.

    The HTTP layer's only entry point into this module, and the only place the
    header is read. It separates two failures the rule would otherwise merge: an
    absent header (no credential was presented at all) and a header that is not
    one Bearer token (a credential was presented that this application does not
    read — `Basic`, a bare token, a comma-joined list, a token with whitespace in
    it). Both refuse; the caller can tell them apart.

    Nothing else about the request is consulted — not a query parameter, not a
    form field, not a cookie, not a session variable — so no other input can
    establish an identity.
    """
    if authorization is None or (isinstance(authorization, str) and not authorization.strip()):
        raise IdentityRefused(
            IDENTITY_REFUSAL_NO_TOKEN,
            "no access token was presented, so no reviewer was identified.",
        )
    token = bearer_token(authorization)
    if token is None:
        raise IdentityRefused(
            IDENTITY_REFUSAL_MALFORMED_TOKEN,
            "the Authorization header is not a single `Bearer <access token>` credential, so no "
            "reviewer was identified.",
        )
    return verify_access_token(token, verifier=verifier, issuer=issuer, audience=audience)
