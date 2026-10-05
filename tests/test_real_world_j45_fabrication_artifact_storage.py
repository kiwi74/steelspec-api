"""
J45 — FABRICATION ARTIFACT WORKING DIRECTORY + DURABLE STORAGE INTEGRATION.

What this file is for
---------------------
A fabricated drawing is a deliverable. Two things follow, and both are what these
tests are about: the bytes that get stored must be the bytes that VERIFIED, and
the place they get stored must be a place no caller chose.

So the properties pinned here are refusals and derivations, not features:

  * the working directory has NO default, must be absolute, and must be usable —
    a fabricated drawing must not land in whatever directory the process started
    in, nor in a shared /tmp;
  * a workspace is derived from server-controlled identifiers and is asserted to
    still be inside the working directory after resolution, which is what makes
    the identifier rule load-bearing rather than decorative;
  * the durable path is a pure function of three identifiers, contains no
    timestamp and no reviewer, and repeats exactly;
  * only a VERIFIED artifact is uploaded, and the refusal happens BEFORE the
    storage call, so an unverified drawing never reaches the bucket and can never
    be pointed at by the project's fabrication path;
  * the bucket and the path are the module's, never the caller's.

Everything except the last class runs against a double. Only the controlled
storage test touches the real bucket, and it deletes what it creates.

The six mutations at the end are not extra tests: they are the demonstration that
the guards above would FAIL if the property they protect were removed. A guard
that cannot fail is not a guard.
"""
from __future__ import annotations

import ast
import inspect
import os
import pathlib
import types

import pytest

from app.production_fabrication_artifact import (
    ARTIFACT_REFUSED_NOT_A_PDF,
    ARTIFACT_REFUSED_NOT_VERIFIED,
    ARTIFACT_REFUSED_NO_WORKING_DIR,
    ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
    ARTIFACT_REFUSED_UNSAFE_IDENTIFIER,
    ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR,
    ArtifactRefused,
    FABRICATION_BUCKET,
    FABRICATION_CONTENT_TYPE,
    FABRICATION_OBJECT_NAME,
    MAX_SEGMENT_LENGTH,
    PDF_MAGIC,
    VERIFIED_STATUS,
    WORKING_DIR_ENV_VAR,
    connection_workspace,
    durable_object_path,
    local_artifact_path,
    upload_verified_artifact,
    working_directory,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
MODULE_PATH = REPO / "app" / "production_fabrication_artifact.py"
CONFIG_PATH = REPO / "app" / "config.py"
VERIFICATION_AUTHORITY = REPO / "app" / "cad_engine" / "drawing_output_verification.py"
REVIEW_PACKAGE = REPO / "app" / "production_review"

USER_ID = "11111111-2222-4333-8444-555555555555"
PROJECT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_PROJECT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-ffffffffffff"
CONNECTION_ID = "RP-0001"
OTHER_CONNECTION_ID = "RP-0002"

PDF = b"%PDF-1.4\n% a verified fabrication drawing\n%%EOF\n"

# Every value that is an identifier in this schema could be one of these, and
# every one of them must be refused rather than turned into a path.
HOSTILE_IDENTIFIERS = [
    "../../etc/passwd",
    "..",
    ".",
    "a/b",
    "a\\b",
    "/absolute",
    "trailing/",
    "with\x00nul",
    "with\nnewline",
    "with\x1b[31mescape",
    " ",
    "",
    "x" * (MAX_SEGMENT_LENGTH + 1),
    "-leading-dash",
    "_leading-underscore",
    None,
    5,
    b"bytes",
    ["list"],
]


def _code_only(path: pathlib.Path) -> str:
    """The module's code, with every docstring removed and comments gone.

    Read as code so that a property cannot be satisfied by prose: a `storage.`
    that appears only in an explanatory paragraph is not a storage call, and one
    that appears in code is, whether or not it is described.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def strip(node):
        for child in ast.walk(node):
            if isinstance(
                child,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                body = child.body
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    child.body = body[1:] or [ast.Pass()]

    strip(tree)
    return ast.unparse(tree)


class _StorageDouble:
    """What `client.storage.from_(bucket)` returns, recording every call."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.uploads: list[tuple[str, bytes, dict]] = []
        self.error = error

    def upload(self, path, content, file_options=None):
        self.uploads.append((path, content, dict(file_options or {})))
        if self.error is not None:
            raise self.error
        return {"path": path}


class _ClientDouble:
    """What the module's `client` parameter receives. Records the buckets asked for."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.buckets: list[str] = []
        self.storage_object = _StorageDouble(error=error)

    @property
    def storage(self):
        return self

    def from_(self, bucket):
        self.buckets.append(bucket)
        return self.storage_object


def _app_config():
    """`app.config`, importable.

    `app.config` reads `os.environ["SUPABASE_URL"]` at IMPORT, so a bare pytest
    process cannot import it at all. The repository's `.env` fills the keys this
    process does not already have — `load_dotenv` does not override, so a real
    environment always wins. When there is no `.env` either, the test skips
    rather than fails: the subject is the wiring, not the presence of credentials.
    """
    try:
        import dotenv

        dotenv.load_dotenv(REPO / ".env")
        import app.config as config
    except KeyError as exc:
        pytest.skip(f"app.config reads {exc} at import and no .env is available")
    return config


def _upload(**overrides):
    """Call the upload with valid arguments, overridden by keyword."""
    arguments = dict(
        user_id=USER_ID,
        project_id=PROJECT_ID,
        connection_id=CONNECTION_ID,
        content=PDF,
        verification_status=VERIFIED_STATUS,
        client=_ClientDouble(),
    )
    arguments.update(overrides)
    return upload_verified_artifact(**arguments)


# =============================================================================
# 1. THE WORKING DIRECTORY — no default, absolute, usable.
# =============================================================================
class TestTheWorkingDirectory:
    def test_nothing_configured_is_refused_rather_than_defaulted(self, monkeypatch):
        """There is no fallback. An unset working directory is a refusal, so a
        fabricated drawing cannot quietly land in the process's own directory."""
        config = _app_config()

        monkeypatch.setattr(config, "ARTIFACT_WORKING_DIR", None)
        with pytest.raises(ArtifactRefused) as refused:
            working_directory()
        assert refused.value.code == ARTIFACT_REFUSED_NO_WORKING_DIR

    def test_a_blank_configuration_is_refused_like_an_absent_one(self):
        for blank in ("", "   ", "\t", "\n  "):
            with pytest.raises(ArtifactRefused) as refused:
                working_directory(blank)
            assert refused.value.code == ARTIFACT_REFUSED_NO_WORKING_DIR, blank

    def test_a_relative_directory_is_refused(self, tmp_path, monkeypatch):
        """A relative directory is resolved against the process's cwd, which is
        not a property this layer is willing to inherit."""
        monkeypatch.chdir(tmp_path)
        for relative in ("artifacts", "./artifacts", "a/b/c", "../outside"):
            with pytest.raises(ArtifactRefused) as refused:
                working_directory(relative)
            assert refused.value.code == ARTIFACT_REFUSED_RELATIVE_WORKING_DIR, relative
        assert not (tmp_path / "artifacts").exists(), "the refusal wrote nothing"

    def test_an_absolute_writable_directory_is_accepted_and_returned(self, tmp_path):
        assert working_directory(str(tmp_path)) == tmp_path
        assert tmp_path.is_absolute()

    def test_a_missing_absolute_directory_is_created(self, tmp_path):
        nested = tmp_path / "one" / "two" / "three"
        assert not nested.exists()
        assert working_directory(str(nested)) == nested
        assert nested.is_dir(), "a usable location was created rather than refused"

    def test_a_file_where_a_directory_belongs_is_refused(self, tmp_path):
        occupied = tmp_path / "not-a-directory"
        occupied.write_text("a file")
        with pytest.raises(ArtifactRefused) as refused:
            working_directory(str(occupied))
        assert refused.value.code == ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR

    def test_a_directory_that_cannot_be_created_is_refused(self, tmp_path):
        blocked = tmp_path / "file-blocking-the-path"
        blocked.write_text("a file, so the directory below it cannot exist")
        with pytest.raises(ArtifactRefused) as refused:
            working_directory(str(blocked / "below"))
        assert refused.value.code == ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores the permission bits")
    def test_a_directory_this_process_cannot_write_is_refused(self, tmp_path):
        import stat

        readonly = tmp_path / "readonly"
        readonly.mkdir()
        readonly.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            with pytest.raises(ArtifactRefused) as refused:
                working_directory(str(readonly))
            assert refused.value.code == ARTIFACT_REFUSED_UNUSABLE_WORKING_DIR
        finally:
            readonly.chmod(stat.S_IRWXU)

    def test_the_setting_is_read_from_the_environment_with_no_default(self):
        """The one configuration surface J45 adds. `.get` with a single argument
        is the whole assertion: a second argument would BE the silent default."""
        text = CONFIG_PATH.read_text(encoding="utf-8")
        assert 'ARTIFACT_WORKING_DIR = os.environ.get("ARTIFACT_WORKING_DIR")' in text
        assert WORKING_DIR_ENV_VAR == "ARTIFACT_WORKING_DIR"

    def test_the_configured_value_is_reached_through_app_config(self, tmp_path, monkeypatch):
        config = _app_config()

        wanted = tmp_path / "configured"
        monkeypatch.setattr(config, "ARTIFACT_WORKING_DIR", str(wanted))
        assert working_directory() == wanted
        assert wanted.is_dir()


# =============================================================================
# 2. THE WORKSPACE — derived, isolated, contained.
# =============================================================================
class TestTheWorkspaceIsolation:
    def test_the_workspace_is_the_project_then_the_connection(self, tmp_path):
        assert connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=tmp_path) == (
            tmp_path / PROJECT_ID / CONNECTION_ID
        )

    def test_two_connections_in_one_project_do_not_share_a_workspace(self, tmp_path):
        first = connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=tmp_path)
        second = connection_workspace(PROJECT_ID, OTHER_CONNECTION_ID, working_dir=tmp_path)
        assert first != second
        assert first.is_dir() and second.is_dir()
        assert not first.is_relative_to(second) and not second.is_relative_to(first)

    def test_two_projects_do_not_share_a_workspace(self, tmp_path):
        first = connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=tmp_path)
        second = connection_workspace(OTHER_PROJECT_ID, CONNECTION_ID, working_dir=tmp_path)
        assert first != second
        assert first.parent.name == PROJECT_ID and second.parent.name == OTHER_PROJECT_ID

    def test_the_same_connection_always_resolves_to_the_same_workspace(self, tmp_path):
        assert connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=tmp_path) == (
            connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=tmp_path)
        )

    def test_the_local_artifact_is_named_in_its_own_workspace(self, tmp_path):
        assert local_artifact_path(PROJECT_ID, CONNECTION_ID, working_dir=tmp_path) == (
            tmp_path / PROJECT_ID / CONNECTION_ID / FABRICATION_OBJECT_NAME
        )

    def test_the_local_name_and_the_durable_name_are_the_same_name(self):
        """The artifact does not change identity on the way out."""
        assert durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID).endswith(
            f"/{FABRICATION_OBJECT_NAME}"
        )

    def test_no_workspace_escapes_the_working_directory(self, tmp_path):
        for hostile in HOSTILE_IDENTIFIERS:
            for call in (
                lambda value: connection_workspace(PROJECT_ID, value, working_dir=tmp_path),
                lambda value: local_artifact_path(value, CONNECTION_ID, working_dir=tmp_path),
            ):
                with pytest.raises(ArtifactRefused) as refused:
                    call(hostile)
                assert refused.value.code == ARTIFACT_REFUSED_UNSAFE_IDENTIFIER, hostile

    def test_a_connection_identifier_is_one_segment_and_nothing_else(self, tmp_path):
        """The identifiers that are legitimate are exactly the ones that are one
        segment: no separator of either kind can appear, so no identifier can
        name a directory that is not its own."""
        for legitimate in ("RP-0001", "RP_0002", "abc.def", "ABC:1", "0", "a" * 200):
            workspace = connection_workspace(PROJECT_ID, legitimate, working_dir=tmp_path)
            assert workspace.parent == tmp_path / PROJECT_ID
            assert workspace.name == legitimate

    def test_a_project_identifier_must_be_a_canonical_uuid(self, tmp_path):
        """`projects.id` is `uuid NOT NULL`; a canonical lower-case UUID is what a
        correct caller holds, and it cannot contain a separator at all."""
        for not_a_uuid in ("RP-0001", PROJECT_ID.upper(), PROJECT_ID[:-1],
                           PROJECT_ID + "0", "{" + PROJECT_ID + "}", None, 7):
            with pytest.raises(ArtifactRefused) as refused:
                connection_workspace(not_a_uuid, CONNECTION_ID, working_dir=tmp_path)
            assert refused.value.code == ARTIFACT_REFUSED_UNSAFE_IDENTIFIER

    def test_a_workspace_that_resolves_outside_the_working_directory_is_refused(self, tmp_path):
        """The segment rules already forbid a separator; this is the assertion
        that holds on the RESOLVED path, so a symlink planted where a workspace
        belongs cannot be walked through either."""
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "root"
        (root / PROJECT_ID).mkdir(parents=True)
        (root / PROJECT_ID / CONNECTION_ID).symlink_to(outside / CONNECTION_ID)

        with pytest.raises(ArtifactRefused) as refused:
            connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=root)
        assert refused.value.code == ARTIFACT_REFUSED_UNSAFE_IDENTIFIER
        assert not (outside / CONNECTION_ID).exists(), "nothing was created outside the root"


# =============================================================================
# 3. THE DURABLE PATH — deterministic, server-derived, pure.
# =============================================================================
class TestTheDurableObjectPath:
    def test_the_path_is_exactly_the_documented_shape(self):
        assert durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID) == (
            f"{USER_ID}/{PROJECT_ID}/{CONNECTION_ID}/{FABRICATION_OBJECT_NAME}"
        )

    def test_the_first_segment_is_the_owner(self):
        """Not decoration: every deployed policy on `storage.objects` requires
        `foldername(name)[1] = auth.uid()`, so this is the segment that decides
        whose browser session may read the object."""
        for owner in (USER_ID, "99999999-8888-4777-8666-555555555555"):
            assert durable_object_path(owner, PROJECT_ID, CONNECTION_ID).split("/")[0] == owner

    def test_repeating_the_call_gives_the_identical_string(self):
        first = durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        for _ in range(5):
            assert durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID) == first

    def test_the_path_carries_no_timestamp_and_no_reviewer(self):
        """Two reviewers of one connection must not produce two objects, and a
        re-run must overwrite its own object rather than add another.

        Asserted structurally — the module consults no clock at all — because a
        path that happened to look right today would satisfy any weaker check."""
        code = _code_only(MODULE_PATH)
        for clock in ("time", "datetime", "uuid", "now", "timestamp"):
            assert clock not in code, f"the path is derived from a {clock}"

        path = durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        assert path.count("/") == 3
        assert all(path.split("/")), "no empty segment"
        assert path.endswith(f"/{FABRICATION_OBJECT_NAME}")

    def test_hostile_identifiers_never_become_a_path(self):
        for hostile in HOSTILE_IDENTIFIERS:
            for call in (
                lambda value: durable_object_path(value, PROJECT_ID, CONNECTION_ID),
                lambda value: durable_object_path(USER_ID, value, CONNECTION_ID),
                lambda value: durable_object_path(USER_ID, PROJECT_ID, value),
            ):
                with pytest.raises(ArtifactRefused) as refused:
                    call(hostile)
                assert refused.value.code == ARTIFACT_REFUSED_UNSAFE_IDENTIFIER, hostile

    def test_the_path_computation_touches_nothing(self, tmp_path, monkeypatch):
        """Pure: no file is created, no working directory is required, and the
        function is callable with no configuration in the environment at all."""
        config = _app_config()
        monkeypatch.setattr(config, "ARTIFACT_WORKING_DIR", None)
        monkeypatch.chdir(tmp_path)
        before = sorted(pathlib.Path(tmp_path).iterdir())
        assert durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        assert sorted(pathlib.Path(tmp_path).iterdir()) == before

    def test_the_function_takes_nothing_but_identifiers(self):
        assert list(inspect.signature(durable_object_path).parameters) == [
            "user_id", "project_id", "connection_id",
        ]


# =============================================================================
# 4. VERIFICATION — only a VERIFIED artifact may be uploaded.
# =============================================================================
class TestOnlyAVerifiedArtifactIsUploaded:
    def test_a_verified_pdf_is_uploaded_to_the_durable_path(self):
        client = _ClientDouble()
        path = _upload(client=client)
        assert path == durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        assert client.buckets == [FABRICATION_BUCKET]
        assert client.storage_object.uploads == [
            (path, PDF, {"content-type": FABRICATION_CONTENT_TYPE, "upsert": "true"})
        ]

    def test_anything_that_is_not_verified_is_refused(self):
        """Every other status this system can produce, plus the shapes a caller
        might pass by accident. None of them is `VERIFIED`, so none is uploaded."""
        for status in ("FAILED", "NOT_VERIFIABLE", "NO_ARTIFACT", "failed", "Verified",
                       " VERIFIED", "VERIFIED ", "", None, True, 1):
            client = _ClientDouble()
            with pytest.raises(ArtifactRefused) as refused:
                _upload(verification_status=status, client=client)
            assert refused.value.code == ARTIFACT_REFUSED_NOT_VERIFIED, status
            assert client.buckets == [], f"{status!r} reached storage"
            assert client.storage_object.uploads == [], f"{status!r} was uploaded"

    def test_the_refusal_happens_before_the_storage_call(self):
        """The distinction that matters: not 'the upload failed' but 'no upload
        was attempted'. A refusal after the call would already have put the
        unverified bytes in the bucket."""
        client = _ClientDouble()
        with pytest.raises(ArtifactRefused):
            _upload(verification_status="FAILED", client=client)
        assert client.buckets == []

    def test_the_status_is_required_and_has_no_default(self):
        """No default, so a drawing cannot be uploaded by omission."""
        parameters = inspect.signature(upload_verified_artifact).parameters
        assert parameters["verification_status"].default is inspect.Parameter.empty
        with pytest.raises(TypeError):
            upload_verified_artifact(
                user_id=USER_ID, project_id=PROJECT_ID, connection_id=CONNECTION_ID,
                content=PDF, client=_ClientDouble(),
            )

    def test_there_is_no_durable_store_for_a_failed_artifact(self):
        """A failed drawing is a result the caller reports, not a deliverable.
        No bucket and no path in this module can hold one."""
        import app.production_fabrication_artifact as module

        strings = {
            name: value
            for name, value in vars(module).items()
            if name.isupper() and isinstance(value, str)
        }
        assert not [n for n, v in strings.items() if "fail" in v.lower()], strings
        assert [v for v in strings.values() if v.endswith("-drawings")] == [FABRICATION_BUCKET]

    def test_the_verified_constant_is_the_verification_authoritys_own_word(self):
        """One source of truth, pinned by text: the authority imports pypdf and
        the CAD engine, which this storage layer must not depend on, so the two
        definitions are compared as they are written rather than by importing."""
        authority = VERIFICATION_AUTHORITY.read_text(encoding="utf-8")
        assert f'VERIFICATION_STATUS_VERIFIED = "{VERIFIED_STATUS}"' in authority


# =============================================================================
# 5. THE UPLOAD CONTRACT — content type, overwrite, errors, no retry.
# =============================================================================
class TestTheUploadContract:
    def test_the_content_type_is_explicit_and_is_a_pdf(self):
        client = _ClientDouble()
        _upload(client=client)
        _, _, options = client.storage_object.uploads[0]
        assert options["content-type"] == "application/pdf" == FABRICATION_CONTENT_TYPE

    def test_the_upload_overwrites_its_own_object_rather_than_erroring(self):
        client = _ClientDouble()
        first = _upload(client=client)
        second = _upload(content=b"%PDF-1.5\n% regenerated\n%%EOF\n", client=client)
        assert first == second, "the same connection must reuse its own path"
        assert [call[0] for call in client.storage_object.uploads] == [first, second]
        assert all(call[2]["upsert"] == "true" for call in client.storage_object.uploads)

    def test_the_bytes_uploaded_are_the_bytes_given(self):
        payload = b"%PDF-1.7\n%\xe2\x9c\x93 bit-exact\n%%EOF\n"
        client = _ClientDouble()
        _upload(content=payload, client=client)
        assert client.storage_object.uploads[0][1] == payload

    def test_content_that_is_not_a_pdf_is_refused(self):
        for not_a_pdf in (b"", b"not a pdf", b"<html>", b"PK\x03\x04", b" %PDF-", b"\n%PDF-"):
            client = _ClientDouble()
            with pytest.raises(ArtifactRefused) as refused:
                _upload(content=not_a_pdf, client=client)
            assert refused.value.code == ARTIFACT_REFUSED_NOT_A_PDF, not_a_pdf
            assert client.storage_object.uploads == []

    def test_a_path_is_not_content(self):
        """This layer uploads bytes. It never opens a file the caller named, so
        no request body can steer a read."""
        for not_bytes in ("/etc/passwd", pathlib.Path("/tmp/x.pdf"), None, 42, ["x"]):
            client = _ClientDouble()
            with pytest.raises(ArtifactRefused) as refused:
                _upload(content=not_bytes, client=client)
            assert refused.value.code == ARTIFACT_REFUSED_NOT_A_PDF
            assert client.storage_object.uploads == []

    def test_a_storage_error_propagates_unchanged_and_no_path_is_returned(self):
        class StorageRefused(RuntimeError):
            pass

        error = StorageRefused("the storage API answered 500")
        client = _ClientDouble(error=error)
        with pytest.raises(StorageRefused) as raised:
            _upload(client=client)
        assert raised.value is error, "the error must not be translated or wrapped"

    def test_a_failed_upload_is_not_retried_and_is_not_silently_successful(self):
        class StorageRefused(RuntimeError):
            pass

        client = _ClientDouble(error=StorageRefused("nope"))
        with pytest.raises(StorageRefused):
            _upload(client=client)
        assert len(client.storage_object.uploads) == 1, "exactly one attempt, never a retry"

    def test_the_identifiers_are_validated_before_the_storage_call(self):
        client = _ClientDouble()
        with pytest.raises(ArtifactRefused) as refused:
            _upload(connection_id="../../escape", client=client)
        assert refused.value.code == ARTIFACT_REFUSED_UNSAFE_IDENTIFIER
        assert client.buckets == []


# =============================================================================
# 6. SECURITY — the bucket and the path are the module's, never the caller's.
# =============================================================================
class TestTheCallerChoosesNothing:
    def test_no_parameter_can_name_a_bucket(self):
        parameters = inspect.signature(upload_verified_artifact).parameters
        assert list(parameters) == [
            "user_id", "project_id", "connection_id", "content",
            "verification_status", "client",
        ]
        assert not [name for name in parameters if name in ("bucket", "path", "key", "object_path")]

    def test_the_bucket_is_the_modules_own_constant(self):
        client = _ClientDouble()
        _upload(client=client)
        assert client.buckets == [FABRICATION_BUCKET] == ["fabrication-drawings"]

    def test_a_bucket_in_the_environment_cannot_redirect_the_upload(self, monkeypatch):
        """Belt and braces for the mutation below: nothing in the module reads a
        bucket from anywhere but its own constant."""
        code = _code_only(MODULE_PATH)
        assert "environ" not in code, "the module reads no configuration but the working directory"
        monkeypatch.setenv("J45_MUTANT_BUCKET", "uploads")
        client = _ClientDouble()
        _upload(client=client)
        assert client.buckets == [FABRICATION_BUCKET]

    def test_no_public_or_signed_url_is_ever_constructed(self):
        """The object is private. Nothing here needs a URL, and a URL would be a
        second, weaker way to reach the artifact."""
        code = _code_only(MODULE_PATH)
        assert "get_public_url" not in code
        assert "create_signed_url" not in code
        assert "public" not in code.replace("publicly", "")

    def test_the_module_creates_no_second_client_and_holds_no_credential(self):
        code = _code_only(MODULE_PATH)
        assert "create_client" not in code
        assert "SERVICE_ROLE" not in code
        assert "Authorization" not in code

    def test_the_client_is_imported_on_use_not_at_import(self):
        """The module must import in an environment with no credentials, so the
        package can be read for its contract without one."""
        code = _code_only(MODULE_PATH)
        assert code.index("def upload_verified_artifact") < code.index(
            "from app.supabase_client import supabase"
        )
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                assert not [a.name for a in node.names if a.name.startswith("app")]
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("app"), node.module

    def test_a_hostile_identifier_is_refused_without_echoing_it_raw(self, tmp_path):
        """A refusal is logged. A refusal that can forge log lines is worse than
        no message, so control characters are rendered rather than emitted."""
        payload = "bad\nINJECTED: a line that is not this module's\x1b[31m"
        with pytest.raises(ArtifactRefused) as refused:
            durable_object_path(USER_ID, PROJECT_ID, payload)
        message = str(refused.value)
        assert "\n" not in message, "the value cannot add a line to the message"
        assert "\x1b" not in message, "a terminal escape must not survive into the message"
        assert "bad?INJECTED" in message, "the value is shown, rendered, not echoed raw"


# =============================================================================
# 7. ISOLATION — what this module deliberately does not do.
# =============================================================================
class TestTheModulesBoundaries:
    def test_it_is_not_in_the_review_package(self):
        """J19 pinned that package as reaching no model, no file and no network.
        An uploader inside it would break the boundary that makes it safe to
        import, so this module sits beside it."""
        assert not (REVIEW_PACKAGE / "fabrication_artifact_storage.py").exists()
        assert not (REVIEW_PACKAGE / "production_fabrication_artifact.py").exists()
        assert MODULE_PATH.parent == REPO / "app"

    def test_the_review_package_is_unchanged(self):
        """J45 adds a module BESIDE the package, not inside it: no module in the
        package names this layer, so the boundary J19 drew still holds."""
        for path in sorted(REVIEW_PACKAGE.glob("*.py")):
            code = _code_only(path)
            assert "fabrication" not in code.lower(), path.name
            assert FABRICATION_BUCKET not in code, path.name

    def test_it_writes_no_database_row_at_all(self):
        """Not 'it does not write the fabrication pointer' — it makes no table
        call, so the pointer cannot be populated by this milestone even by
        accident. Wiring the pointer is a later milestone's step."""
        code = _code_only(MODULE_PATH)
        assert ".table(" not in code
        assert ".update(" not in code
        assert ".insert(" not in code
        assert ".execute(" not in code

    def test_it_exposes_no_route(self):
        code = _code_only(MODULE_PATH)
        assert "fastapi" not in code.lower()
        assert "APIRouter" not in code
        assert "Depends" not in code

    def test_it_resolves_records_and_claims_nothing(self):
        code = _code_only(MODULE_PATH)
        for foreign in ("resolve_project_connection", "acquire_project_review_claim",
                        "release_project_review_claim", "snapshot", "generated_files",
                        "authorize_project"):
            assert foreign not in code, foreign

    def test_it_generates_verifies_and_extracts_nothing(self):
        """J45 is the storage layer. Generating a drawing, verifying one and
        reading a PDF all have owners already, and this module holds no copy."""
        code = _code_only(MODULE_PATH)
        for foreign in ("pypdf", "fitz", "anthropic", "subprocess", "extract",
                        "cad_engine", "update_project_status"):
            assert foreign not in code, foreign

    def test_its_only_write_is_the_upload(self):
        """The module's single effect is one object in one bucket. The working
        directory is created because a workspace that cannot be written to is not
        a workspace; nothing else in it is touched."""
        code = _code_only(MODULE_PATH)
        writes = [node for node in ast.walk(ast.parse(code))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr in ("mkdir", "write_text", "write_bytes", "upload")]
        assert sorted(node.func.attr for node in writes) == ["mkdir", "mkdir", "upload"], (
            sorted(node.func.attr for node in writes)
        )

    def test_the_repository_carries_no_pdf_artifact(self):
        """The working directory is configured, never the repository."""
        for directory in (REPO / "app", REPO / "tests"):
            assert not [p for p in directory.rglob("*.pdf")], directory

    def test_only_the_declared_modules_import_this_module(self):
        """J45 stops before the production route. Until the route exists this
        module is imported by path, and its only consumer is this test file —
        a second one must be declared here rather than appear quietly.

        J47 IS that route, so the wiring this test was holding open has now
        happened and the set below is declared rather than empty. The guard is
        not weakened by declaring it: the assertion is still EXACT, so a fourth
        importer — a second storage path, or a caller that reaches this module
        without going through the reviewed route — still turns this red. What
        changed is only that the three deliberate importers are now named.

        * `app/main.py` — the J47 route, and the only production caller.
        * `app/production_review_resolution.py` — the J47 composition, which
          uploads and reads the working directory.
        * `app/production_fabrication_pointer.py` — the pointer writer, which
          derives the durable identity with J45's own function rather than
          re-implementing the path rule (see that module's docstring).
        """
        importers = []
        for path in sorted((REPO / "app").rglob("*.py")):
            if path == MODULE_PATH:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                module = None
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = ",".join(alias.name for alias in node.names)
                if module and "production_fabrication_artifact" in module:
                    importers.append(str(path.relative_to(REPO)))
        # A prose pointer is not an importer: `app/config.py` names this module
        # where it documents the setting. An import is what would make this a
        # wired feature.
        assert importers == [
            "app/main.py",
            "app/production_fabrication_pointer.py",
            "app/production_review_resolution.py",
        ], importers


# =============================================================================
# 8. THE SIX MUTATIONS — the guards above are shown to be able to fail.
# =============================================================================
def _mutant(old: str, new: str) -> types.ModuleType:
    """The module with one exact edit applied, executed in isolation.

    Source-level, so the mutation is the property being removed rather than a
    behaviour being faked, and isolated, so the real module is untouched.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert source.count(old) == 1, f"the mutation anchor is not unique: {old!r}"
    module = types.ModuleType("j45_mutant")
    exec(compile(source.replace(old, new), "<j45-mutant>", "exec"), module.__dict__)
    return module


_RELATIVE_GUARD = '''    if not path.is_absolute():
        raise ArtifactRefused(
            ARTIFACT_REFUSED_RELATIVE_WORKING_DIR,
            f"the artifact working directory must be absolute; got {_describe(working_dir)}",
        )
'''

_CONTAINMENT_GUARD = '''    if not workspace.is_relative_to(root.resolve()):
        raise ArtifactRefused(
            ARTIFACT_REFUSED_UNSAFE_IDENTIFIER,
            f"the workspace for project_id={_describe(project_id)} "
            f"connection_id={_describe(connection_id)} resolves outside the "
            f"working directory",
        )
'''

_VERIFICATION_GUARD = '''    if verification_status != VERIFIED_STATUS:
        raise ArtifactRefused(
            ARTIFACT_REFUSED_NOT_VERIFIED,
            f"only a {VERIFIED_STATUS} artifact may be uploaded; got "
            f"{_describe(verification_status)}. A drawing that did not verify is "
            f"reported to the caller, never stored.",
        )
'''


class TestTheGuardsCanFail:
    """Each mutation removes one property; each test shows a guard catching it.

    Every mutation is applied to a COPY of the source. The module under test in
    every other class is untouched, and this class asserts the mutant's behaviour
    differs — which is what makes the guards above evidence rather than prose.
    """

    def test_mutation_a_removing_the_absolute_path_rule_is_caught(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ArtifactRefused) as refused:
            working_directory("relative-artifacts")
        assert refused.value.code == ARTIFACT_REFUSED_RELATIVE_WORKING_DIR

        mutant = _mutant(_RELATIVE_GUARD, "")
        # The mutant happily resolves a relative directory against the cwd.
        assert not mutant.working_directory("relative-artifacts").is_absolute()
        assert (tmp_path / "relative-artifacts").is_dir()
        # Which is precisely what the guard above refused.
        assert not (tmp_path / "artifacts-created-by-the-guard").exists()

    def test_mutation_b_a_timestamp_instead_of_a_deterministic_path_is_caught(self):
        stable = durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        assert durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID) == stable

        mutant = _mutant(
            '    return f"{owner}/{project}/{connection}/{FABRICATION_OBJECT_NAME}"',
            '    import uuid as _u\n'
            '    return f"{owner}/{project}/{connection}/{_u.uuid4().hex}-{FABRICATION_OBJECT_NAME}"',
        )
        first = mutant.durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        second = mutant.durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID)
        assert first != second, "the mutant produces a new object per call"
        # The guard that catches it: repetition returns the identical string.
        assert durable_object_path(USER_ID, PROJECT_ID, CONNECTION_ID) == stable

    def test_mutation_c_a_caller_supplied_bucket_is_caught(self, monkeypatch):
        client = _ClientDouble()
        _upload(client=client)
        assert client.buckets == [FABRICATION_BUCKET]

        mutant = _mutant(
            "    client.storage.from_(FABRICATION_BUCKET).upload(",
            '    client.storage.from_(os.environ.get("J45_MUTANT_BUCKET")'
            " or FABRICATION_BUCKET).upload(",
        )
        monkeypatch.setenv("J45_MUTANT_BUCKET", "uploads")
        steered = _ClientDouble()
        mutant.upload_verified_artifact(
            user_id=USER_ID, project_id=PROJECT_ID, connection_id=CONNECTION_ID,
            content=PDF, verification_status=VERIFIED_STATUS, client=steered,
        )
        assert steered.buckets == ["uploads"], "the mutant lets the environment pick the bucket"
        # The guard that catches it: with the identical environment set, the real
        # module still stores into its own bucket and nowhere else.
        unsteered = _ClientDouble()
        _upload(client=unsteered)
        assert unsteered.buckets == [FABRICATION_BUCKET]

    def test_mutation_d_allowing_an_unverified_upload_is_caught(self):
        client = _ClientDouble()
        with pytest.raises(ArtifactRefused) as refused:
            _upload(verification_status="FAILED", client=client)
        assert refused.value.code == ARTIFACT_REFUSED_NOT_VERIFIED
        assert client.storage_object.uploads == []

        mutant = _mutant(_VERIFICATION_GUARD, "")
        unguarded = _ClientDouble()
        path = mutant.upload_verified_artifact(
            user_id=USER_ID, project_id=PROJECT_ID, connection_id=CONNECTION_ID,
            content=PDF, verification_status="FAILED", client=unguarded,
        )
        assert unguarded.storage_object.uploads == [(path, PDF, {
            "content-type": FABRICATION_CONTENT_TYPE, "upsert": "true"})], (
            "the mutant stores a drawing that failed its own verification"
        )

    def test_mutation_e_dropping_the_content_type_is_caught(self):
        client = _ClientDouble()
        _upload(client=client)
        assert client.storage_object.uploads[0][2]["content-type"] == "application/pdf"

        mutant = _mutant(
            '        file_options={"content-type": FABRICATION_CONTENT_TYPE, "upsert": "true"},',
            '        file_options={"upsert": "true"},',
        )
        untyped = _ClientDouble()
        mutant.upload_verified_artifact(
            user_id=USER_ID, project_id=PROJECT_ID, connection_id=CONNECTION_ID,
            content=PDF, verification_status=VERIFIED_STATUS, client=untyped,
        )
        assert "content-type" not in untyped.storage_object.uploads[0][2], (
            "the mutant lets the declared content type fall back"
        )

    def test_mutation_f_removing_the_traversal_check_is_caught(self, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "root"
        (root / PROJECT_ID).mkdir(parents=True)
        link = root / PROJECT_ID / CONNECTION_ID
        link.symlink_to(outside / CONNECTION_ID)

        with pytest.raises(ArtifactRefused) as refused:
            connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=root)
        assert refused.value.code == ARTIFACT_REFUSED_UNSAFE_IDENTIFIER
        assert not (outside / CONNECTION_ID).exists()

        mutant = _mutant(_CONTAINMENT_GUARD, "")
        escaped = mutant.connection_workspace(PROJECT_ID, CONNECTION_ID, working_dir=root)
        assert not escaped.is_relative_to(root), "the mutant walks the symlink out of the root"
        assert escaped == (outside / CONNECTION_ID).resolve()


# =============================================================================
# 9. THE CONTROLLED STORAGE TEST
# =============================================================================
#: Every object this class creates is filed under this prefix, so a leftover from
#: a crashed run is findable and is never mistaken for a deliverable.
PROBE_PREFIX = "j45-test"

#: The connection id the module's own path is exercised with. It is deliberately
#: not a real connection: the two identifiers above are valid UUIDs that belong
#: to no project, so this test cannot collide with, or overwrite, a genuine
#: artifact — the storage layer has no foreign key that would require otherwise.
PROBE_CONNECTION = "j45-controlled-test"


@pytest.fixture(scope="module")
def live_storage():
    """The real storage client, or a skip. Never a substitute: a controlled
    storage test that ran against a double would have proved nothing about the
    bucket, which is the only thing this class exists to prove."""
    try:
        import dotenv

        dotenv.load_dotenv(REPO / ".env")
        from supabase import create_client

        client = create_client(
            os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        )
        client.storage.get_bucket(FABRICATION_BUCKET)
    except Exception as exc:
        pytest.skip(f"the live fabrication bucket is not reachable: {type(exc).__name__}")
    return client


class TestTheControlledStorageTest:
    """The one class that touches the real bucket.

    Everything it creates it deletes, and it asserts the deletion: a test that
    left a fabrication object behind would be worse than no test at all. It
    touches no other bucket, no project row and no existing object.
    """

    def test_the_bucket_starts_clean_of_test_objects(self, live_storage):
        """A leftover from a crashed run is surfaced here rather than quietly
        accumulating into a bucket that is supposed to hold deliverables only."""
        store = live_storage.storage.from_(FABRICATION_BUCKET)
        assert store.list(PROBE_PREFIX) == []

    def test_the_bucket_is_private_and_admits_only_pdfs(self, live_storage):
        bucket = live_storage.storage.get_bucket(FABRICATION_BUCKET)
        assert bucket.public is False, "a fabricated drawing is never public"
        assert list(bucket.allowed_mime_types) == [FABRICATION_CONTENT_TYPE]

    def test_a_verified_artifact_uploads_downloads_and_deletes_with_no_residue(
        self, live_storage
    ):
        store = live_storage.storage.from_(FABRICATION_BUCKET)
        probe = f"{PROBE_PREFIX}/{os.urandom(6).hex()}/test.pdf"
        created: list[str] = []
        try:
            # (1) the bucket itself, with a disposable object of known type.
            store.upload(
                probe, PDF, file_options={"content-type": FABRICATION_CONTENT_TYPE}
            )
            created.append(probe)
            assert store.exists(probe)
            assert store.download(probe) == PDF, "the bucket round-trips bytes exactly"

            # (2) the module's OWN path, through the real function and the real
            # client — the same call the production route will make.
            returned = upload_verified_artifact(
                user_id=USER_ID,
                project_id=PROJECT_ID,
                connection_id=PROBE_CONNECTION,
                content=PDF,
                verification_status=VERIFIED_STATUS,
                client=live_storage,
            )
            created.append(returned)
            assert returned == durable_object_path(USER_ID, PROJECT_ID, PROBE_CONNECTION)
            assert store.exists(returned)
            assert store.download(returned) == PDF

            # (3) deterministic overwrite: the same connection reuses its object
            # rather than accumulating a second one.
            again = upload_verified_artifact(
                user_id=USER_ID,
                project_id=PROJECT_ID,
                connection_id=PROBE_CONNECTION,
                content=b"%PDF-1.5\n% regenerated\n%%EOF\n",
                verification_status=VERIFIED_STATUS,
                client=live_storage,
            )
            assert again == returned
            assert store.download(returned).startswith(b"%PDF-1.5")
        finally:
            for path in created:
                try:
                    store.remove([path])
                except Exception:  # pragma: no cover - cleanup must not mask the subject
                    pass

        for path in created:
            assert not store.exists(path), f"the test left an object behind: {path}"
        assert store.list(PROBE_PREFIX) == [], "no test object survives this class"
