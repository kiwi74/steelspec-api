"""
Milestone 7AP — the review UI's presentation layer, UX pass.

Renders the existing 7AM view model into plain HTML pages, restyled so a
human reviewer sees the workflow's own questions first: what needs
attention, why, and which missing fact each task asks for. It consumes ONLY
the frozen view-model objects (never the workflow, never the contract
layer, never the filesystem, the network or the environment) plus the
existing answer-payload vocabulary constants, and it computes nothing:
every label, count, blocker, task, evidence string, AI value, action and
status on the pages comes verbatim from the view.

HARD RULES:

  - single render contract: `ProjectReviewView` / `ConnectionReviewView`.
  - no decisions, no counts, no statuses, no inference — everything is
    printed from the view's own fields; the dashboard shows the view's own
    counts and summaries, never recomputed totals.
  - failure states stay what they are: "Generation failed", "Verification
    failed" and "No artifact" are the view's own labels, never replaced;
    the verified banner renders ONLY when the view itself reports
    "Verified" (7AG stays authoritative — file names prove nothing).
  - AI values and evidence are shown byte-for-byte, never interpreted,
    converted or decorated (M12 stays M12, SQ4 12mm stays SQ4 12mm).
  - blocker/task relationships come from the view's own fields
    (blocker.task_type matched against the tasks the view supplies) —
    never rebuilt from codes; a blocker with no related task gets no link.
  - no bypass actions: the only rendered controls map to the view's own
    REVIEW / RESOLVE / REFRESH actions and nothing else.
  - the page-exception surface (J18) renders one Retry control per unread page,
    and ONLY for a page whose own view carries that action: there is no
    project-level retry, no "retry all", and nothing on that page initiates
    anything by being rendered. The retry result banner is green only when the
    view's notice says J17 resolved the page, red when it says J17 did not, and
    neutral when J17 said nothing — the state code is always printed.
  - deterministic: fixed markup, fixed field names, no generated ids, no
    timestamps, no randomness, order exactly as supplied by the view.
  - resolution input widgets are driven solely by each task's authoritative
    `answer_type` (7AC's vocabulary); an unsupported answer type is stated
    as unsupported — never guessed at, never silently accepted.
  - all styling is the local stylesheet embedded in this module; no
    framework, no external assets, no frontend runtime.
"""

import html

from app.cad_engine.exception_resolution import (
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_APPROVE_REVIEW,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_MEMBER_SELECTION,
    ANSWER_PLATE_VALUE,
    ANSWER_POSITION_VALUE,
)
from app.cad_engine.page_exception_contract import ACTION_RETRY_PAGE
from app.cad_engine.page_exception_view_model import PageExceptionListView
from app.cad_engine.review_view_model import (
    ACTION_REFRESH,
    ACTION_RESOLVE,
    ACTION_REVIEW,
    ConnectionReviewView,
    ProjectReviewView,
    TaskView,
)

__all__ = [
    "SUPPORTED_ANSWER_TYPES", "render_annotation_evidence_page",
    "render_connection_detail_page", "render_message_page",
    "render_page_exceptions_page", "render_project_page", "render_unbound_page",
    "render_workflow_review_page", "task_input_names", "unsupported_tasks",
]

# The deliberately small set of answer payload types this vertical slice can
# gather. Everything else is refused outright — never silently accepted.
SUPPORTED_ANSWER_TYPES = frozenset({
    ANSWER_APPROVE_REVIEW,
    ANSWER_AUTOMATION_CONFIRMATION,
    ANSWER_ACKNOWLEDGMENT,
    ANSWER_CONNECTION_IDENTITY,
    ANSWER_POSITION_VALUE,
    ANSWER_MEMBER_SELECTION,
    ANSWER_CONFIRMED_FIELDS,
    ANSWER_ATTACHMENTS_VALUE,
    ANSWER_MEMBER_POSITION_ATTACHMENTS,
    ANSWER_PLATE_VALUE,
    ANSWER_HOLES_VALUE,
    ANSWER_LOCATION_VALUE,
    ANSWER_MATERIAL_VALUE,
})


def _esc(text):
    """HTML-escapes text content; quotes survive so verbatim values like
    'M12' stay readable exactly as the view carries them."""
    return html.escape("" if text is None else str(text), quote=False)


def _esc_attr(text):
    return html.escape("" if text is None else str(text), quote=True)


# --------------------------------------------------------------------------------------
# The local stylesheet — restrained, desktop-first, colour used only alongside text.
# --------------------------------------------------------------------------------------
_STYLE = """
:root { --ink:#22303c; --muted:#5c6b7a; --line:#dde4ea; --paper:#ffffff;
        --ground:#f4f6f8; --link:#1f5c99; --focus:#7ab0e0;
        --attention:#8a5a00; --attention-bg:#fff4dc; --attention-line:#f0dcae;
        --verified:#17603a; --verified-bg:#e4f5ea; --verified-line:#bfe5cd;
        --error:#9c2626; --error-bg:#fdeaea; --error-line:#f2c4c4; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--ground); color: var(--ink);
       font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 24px 20px 64px; }
a { color: var(--link); }
a:hover { color: #14446f; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.brand { font-size: 12px; letter-spacing: .16em; text-transform: uppercase;
         color: var(--muted); font-weight: 700; }
.masthead { padding: 28px 0 4px; }
.masthead h1 { margin: 4px 0 2px; font-size: 28px; line-height: 1.2; }
.meta { color: var(--muted); margin: 0; font-size: 14px; }
.backlink { margin: 12px 0 0; }
.summary { display: flex; gap: 12px; margin: 14px 0 8px; flex-wrap: wrap; }
.stat { background: var(--paper); border: 1px solid var(--line); border-radius: 10px;
        padding: 12px 18px; min-width: 130px; }
.stat strong { display: block; font-size: 26px; line-height: 1.15; }
.stat span { color: var(--muted); font-size: 13px; }
.stat-attention strong { color: var(--attention); }
.stat-verified strong { color: var(--verified); }
.summary-line, .counts-line { color: var(--muted); font-size: 13px; margin: 4px 0; }
h2 { font-size: 18px; margin: 30px 0 10px; }
.card { background: var(--paper); border: 1px solid var(--line); border-radius: 10px;
        padding: 16px 18px; margin: 0 0 12px; }
.connection-card { display: block; text-decoration: none; color: inherit; }
.connection-card:hover { border-color: var(--focus); }
.connection-card .row { display: flex; justify-content: space-between;
        align-items: baseline; gap: 12px; flex-wrap: wrap; }
.connection-card .ref { font-size: 17px; font-weight: 700; }
.connection-card .issues { color: var(--attention); font-weight: 700; }
.connection-card .line { color: var(--muted); font-size: 14px; margin-top: 4px; }
.connection-card .go { color: var(--link); font-weight: 700; white-space: nowrap; }
.chip { display: inline-block; padding: 1px 10px; border-radius: 999px; font-size: 12px;
        font-weight: 700; border: 1px solid transparent; white-space: nowrap; }
.chip-attention { background: var(--attention-bg); color: var(--attention);
        border-color: var(--attention-line); }
.chip-verified { background: var(--verified-bg); color: var(--verified);
        border-color: var(--verified-line); }
.chip-error { background: var(--error-bg); color: var(--error);
        border-color: var(--error-line); }
.chip-neutral { background: #eef1f4; color: #46566a; border-color: #d9e0e7; }
.attention-banner { border-left: 5px solid #d99b26; }
.success-banner { border-left: 5px solid #2e9e5b; background: #f2faf5; }
.fail-banner { border-left: 5px solid #c93a3a; background: #fdf6f6; }
.banner-label { font-size: 12px; letter-spacing: .1em; text-transform: uppercase;
        color: var(--muted); font-weight: 700; }
.banner .big { font-size: 19px; font-weight: 700; margin: 4px 0 10px; }
.blocker { padding: 10px 0; border-bottom: 1px solid #eef1f4; }
.blocker:last-child { border-bottom: none; }
.blocker .head { display: flex; justify-content: space-between; gap: 10px;
        align-items: baseline; flex-wrap: wrap; }
.blocker h3 { font-size: 15px; margin: 0; }
.blocker p { margin: 4px 0; color: #46566a; }
.blocker .meta { font-size: 13px; }
dl.meta-list { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px;
        margin: 10px 0; }
dl.meta-list dt, .kv dt { color: var(--muted); font-weight: 600; }
dl.meta-list dd, .kv dd { margin: 0; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 2px 16px; }
.kv dd { overflow-wrap: anywhere; }
.note { font-size: 13px; color: var(--muted); }
.evidence { font-size: 16px; }
ol.tasks { list-style: none; margin: 0; padding: 0; counter-reset: task; }
.task { background: var(--paper); border: 1px solid var(--line);
        border-left: 4px solid #d99b26; border-radius: 10px;
        padding: 14px 18px; margin: 0 0 12px; }
.task.answered { border-left-color: #2e9e5b; }
.task .t-head { display: flex; justify-content: space-between; gap: 10px;
        align-items: baseline; flex-wrap: wrap; }
.task h3 { font-size: 15px; margin: 0; }
.task h3::before { counter-increment: task; content: counter(task) ". "; color: var(--muted); }
.task .desc { margin: 6px 0; color: #46566a; }
.task .task-meta, .task .current { font-size: 13px; color: var(--muted); margin: 3px 0; }
.input-row { margin-top: 10px; }
.input-row label, .field-label { display: block; font-size: 13px; font-weight: 700;
        color: #46566a; margin: 8px 0 3px; }
.task input[type="text"], .task textarea, .card textarea {
        width: 100%; max-width: 540px; padding: 8px 10px; border: 1px solid #b9c5d0;
        border-radius: 6px; font: inherit; background: var(--paper); }
.task textarea, .card textarea { min-height: 64px; }
.btn { display: inline-block; border: 1px solid #b9c5d0; background: var(--paper);
        color: var(--ink); border-radius: 8px; padding: 9px 16px; font: inherit;
        font-weight: 700; cursor: pointer; text-decoration: none; }
.btn:hover { border-color: var(--focus); }
.btn-primary { background: var(--link); border-color: var(--link); color: #ffffff; }
.btn-primary:hover { background: #14446f; }
.actions { margin: 12px 0; }
.unsupported { color: var(--error); font-weight: 700; }
.file-list { font-size: 13px; color: #46566a; margin: 4px 0 0; }
.msg-page .card { max-width: 640px; }
table.evidence { border-collapse: collapse; width: 100%; background: var(--paper);
        border: 1px solid var(--line); border-radius: 10px; font-size: 13px; }
table.evidence th, table.evidence td { text-align: left; vertical-align: top;
        padding: 6px 10px; border-bottom: 1px solid #eef1f4; white-space: nowrap; }
table.evidence th { color: var(--muted); font-weight: 600; }
table.evidence td:last-child { white-space: normal; min-width: 220px; }
table.evidence pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 4px 0 0;
        font-size: 12px; color: #46566a; }
table.evidence details summary { cursor: pointer; color: var(--link); }
@media (max-width: 640px) {
  main { padding: 16px 12px 48px; }
  .summary { gap: 8px; }
  .stat { min-width: 100px; padding: 10px 14px; }
  dl.meta-list, .kv { grid-template-columns: 1fr; }
  .task input[type="text"], .task textarea, .card textarea { max-width: 100%; }
}
"""


def _page(title, body):
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_esc(title)}</title><style>{_STYLE}</style></head><body>"
        f"{body}"
        "</body></html>"
    )


# --------------------------------------------------------------------------------------
# Small presentational atoms — text always accompanies colour, order comes from the view.
# --------------------------------------------------------------------------------------
_CHIP_TONES = {
    "Needs review": "chip-attention",
    "Needs attention": "chip-attention",
    "Needs confirmation": "chip-attention",
    "Blocked from output": "chip-attention",
    "Blocking": "chip-attention",
    "Warning": "chip-neutral",
    "Automated": "chip-neutral",
    "Generated": "chip-neutral",
    "Verified": "chip-verified",
    "Generation failed": "chip-error",
    "Verification failed": "chip-error",
    "No artifact": "chip-error",
    "answered": "chip-verified",
    "unanswered": "chip-neutral",
    # J25's action availability, which the composition decides and this layer only shows.
    "AVAILABLE": "chip-verified",
    "UNAVAILABLE": "chip-attention",
}


def _chip(label):
    """One status label as a chip; the tone is a presentational mapping of the
    view's own label text — the text itself is always shown, never colour alone."""
    return (
        f'<span class="chip {_CHIP_TONES.get(str(label), "chip-neutral")}">{_esc(label)}</span>'
    )


def _stat(label, value, tone=""):
    return (
        f'<div class="stat{tone}"><strong>{_esc(value)}</strong><span>{_esc(label)}</span></div>'
    )


# --------------------------------------------------------------------------------------
# Resolution form field naming — the one convention shared with the submission layer.
# --------------------------------------------------------------------------------------
def task_input_names(task: TaskView) -> tuple[str, ...]:
    """The deterministic form field names a task's inputs are submitted under."""
    if task.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        return (
            f"task_{task.task_id}_marks",
            f"task_{task.task_id}_position",
            f"task_{task.task_id}_attachments",
        )
    return (f"task_{task.task_id}",)


def unsupported_tasks(connection: ConnectionReviewView) -> tuple[TaskView, ...]:
    """The connection's tasks whose answer type this slice cannot gather."""
    return tuple(t for t in connection.tasks if t.answer_type not in SUPPORTED_ANSWER_TYPES)


# --------------------------------------------------------------------------------------
# Per-task input widgets — driven by the task's own answer_type, nothing else.
# Each control carries a label (and matching id) so the form stays accessible;
# field names are exactly the ones the submission layer reads.
# --------------------------------------------------------------------------------------
def _labelled(name, text, control):
    return (
        f'<div class="input-row"><label for="{_esc_attr(name)}">{_esc(text)}</label>'
        f"{control}</div>"
    )


def _text_input(name, placeholder=""):
    placeholder_attr = f' placeholder="{_esc_attr(placeholder)}"' if placeholder else ""
    return f'<input type="text" id="{_esc_attr(name)}" name="{_esc_attr(name)}"{placeholder_attr}>'


def _textarea(name, placeholder=""):
    placeholder_attr = f' placeholder="{_esc_attr(placeholder)}"' if placeholder else ""
    return f'<textarea id="{_esc_attr(name)}" name="{_esc_attr(name)}"{placeholder_attr}></textarea>'


def _task_inputs(task: TaskView) -> str:
    names = task_input_names(task)
    hint = f" (choices: {', '.join(task.allowed_options)})" if task.allowed_options else ""
    if task.answer_type in (ANSWER_APPROVE_REVIEW, ANSWER_AUTOMATION_CONFIRMATION):
        return "<p class=\"note\">No input needed — submitting records the human answer.</p>"
    if task.answer_type == ANSWER_ACKNOWLEDGMENT:
        return _labelled(names[0], "Optional note", _textarea(names[0]))
    if task.answer_type == ANSWER_CONNECTION_IDENTITY:
        return _labelled(names[0], "Connection ID", _text_input(names[0]))
    if task.answer_type == ANSWER_MATERIAL_VALUE:
        return _labelled(names[0], "Material", _text_input(names[0]))
    if task.answer_type == ANSWER_POSITION_VALUE:
        return _labelled(names[0], "Position", _text_input(names[0]) + _esc(hint))
    if task.answer_type in (ANSWER_MEMBER_SELECTION, ANSWER_CONFIRMED_FIELDS):
        return _labelled(
            names[0], "Values",
            _text_input(names[0], "comma-separated values") + _esc(hint),
        )
    if task.answer_type == ANSWER_ATTACHMENTS_VALUE:
        return _labelled(
            names[0], "Attachments",
            _textarea(names[0], "one attachment per line: member_mark|surface_reference"),
        )
    if task.answer_type == ANSWER_MEMBER_POSITION_ATTACHMENTS:
        return (
            _labelled(names[0], "Member marks", _text_input(names[0], "comma-separated member marks"))
            + _labelled(names[1], "Position", _text_input(names[1], "position (START/END)"))
            + _labelled(
                names[2], "Attachments",
                _textarea(names[2], "one attachment per line: member_mark|surface_reference"),
            )
        )
    if task.answer_type in (ANSWER_PLATE_VALUE, ANSWER_HOLES_VALUE, ANSWER_LOCATION_VALUE):
        return _labelled(
            names[0], "Values",
            _textarea(names[0], "one field per line: key: value"),
        )
    return (
        f'<p class="unsupported">This task&rsquo;s answer type ({_esc(task.answer_type)}) is not '
        "supported by this UI yet — the connection cannot be resolved here.</p>"
    )


def _task_sections(tasks, *, interactive=True):
    """The resolution tasks as a numbered sequence of small decisions. Completed
    state comes from the task's own `resolved` field — never invented locally.
    When `interactive` is false the sequence is read-only: the same truth,
    without inputs, for connections the view says cannot be resolved here."""
    sections = []
    for task in tasks:
        state_chip = _chip("answered" if task.resolved else "unanswered")
        details = [f"Task {_esc(task.task_id)}", f"answer type {_esc(task.answer_type)}"]
        if task.field:
            details.append(f"field {_esc(task.field)}")
        if task.required:
            details.append("required")
        current = (
            f'<p class="current">Current AI value: {_esc(task.current_value)}</p>'
            if task.current_value else ""
        )
        choices = (
            f'<p class="task-meta">Choices: {_esc(", ".join(task.allowed_options))}</p>'
            if task.allowed_options else ""
        )
        inputs = _task_inputs(task) if interactive else ""
        sections.append(
            f'<li class="task{" answered" if task.resolved else ""}" id="task-{_esc_attr(task.task_id)}">'
            f'<div class="t-head"><h3>{_esc(task.title)}</h3>{state_chip}</div>'
            f'<p class="desc">{_esc(task.description)}</p>'
            f'<p class="task-meta">{" · ".join(details)}</p>'
            f"{current}{choices}"
            f"{inputs}"
            "</li>"
        )
    return "".join(sections)


# --------------------------------------------------------------------------------------
# Blockers, warnings and their view-supplied relationship to tasks.
# --------------------------------------------------------------------------------------
def _blocker_items(blockers, task_by_type):
    """Blockers as readable items; when the view names the related task type, the
    item links straight to that task's section — the relationship is the view's own."""
    items = []
    for blocker in blockers:
        related = task_by_type.get(blocker.task_type) if blocker.task_type else None
        bits = [f"code {_esc(blocker.code)}"]
        if blocker.field:
            bits.append(f"field {_esc(blocker.field)}")
        if related:
            bits.append(
                f'<a href="#task-{_esc_attr(related)}">Task {_esc(related)} ↓</a>'
            )
        elif blocker.task_type:
            bits.append(f"task {_esc(blocker.task_type)}")
        meta = f'<p class="meta">{" · ".join(bits)}</p>' if bits else ""
        items.append(
            '<div class="blocker">'
            f'<div class="head"><h3>{_esc(blocker.title)}</h3>{_chip(blocker.severity_label)}</div>'
            f"<p>{_esc(blocker.message)}</p>"
            f"{meta}"
            "</div>"
        )
    return "".join(items)


def _extracted_items(extracted):
    """The AI's extracted values, grouped and verbatim — observations, not facts."""
    groups = (
        ("Member references", extracted.member_references),
        ("Bolt readings", extracted.bolt_readings),
        ("Plate readings", extracted.plate_readings),
        ("Weld readings", extracted.weld_readings),
        ("Malformed readings", extracted.malformed_readings),
        ("Unrecognised readings", extracted.unrecognised_readings),
        ("Connection type", extracted.connection_type),
        ("Confidence", extracted.confidence),
        ("Material", extracted.material),
    )
    rows = []
    for label, value in groups:
        if isinstance(value, tuple):
            shown = ", ".join(str(entry) for entry in value) if value else "(none)"
        else:
            shown = str(value) if value else "(none)"
        rows.append(f"<dt>{_esc(label)}</dt><dd>{_esc(shown)}</dd>")
    return "".join(rows)


def _output_section(connection):
    """The output/verification truth from the view alone. The verified banner
    renders ONLY when the view reports 'Verified' — never inferred from files."""
    output = connection.output
    labels = [label for label in (output.output_label, output.verification_label) if label]
    if not labels and not output.generated_files:
        return ""
    if output.verification_label == "Verified":
        banner = (
            '<section class="card success-banner banner">'
            '<div class="banner-label">&#10003; Connection verified</div>'
            f'<p class="big">{_esc(connection.identity.display_reference or connection.identity.package_id)}</p>'
            f'<p>{" ".join(_chip(label) for label in (connection.decision_label, output.output_label, output.verification_label) if label)}</p>'
        )
    elif output.verification_label in ("Verification failed", "No artifact") \
            or output.output_label == "Generation failed":
        banner = (
            '<section class="card fail-banner banner">'
            '<div class="banner-label">Output status</div>'
            f'<p>{" ".join(_chip(label) for label in labels)}</p>'
        )
    else:
        banner = (
            '<section class="card banner">'
            '<div class="banner-label">Output status</div>'
            f'<p>{" ".join(_chip(label) for label in labels)}</p>'
        )
    if output.generated_files:
        files = "<ul class=\"file-list\">" + "".join(
            f"<li>{_esc(name)}</li>" for name in output.generated_files
        ) + "</ul>"
        banner = banner + files
    return banner + "</section>"


# --------------------------------------------------------------------------------------
# Action controls — the view's own actions mapped to the existing operations.
# --------------------------------------------------------------------------------------
def _project_action_controls(actions):
    controls = []
    for action in actions:
        if action.action == ACTION_REFRESH:
            controls.append(
                '<form method="post" action="/refresh"><button class="btn">Refresh</button></form>'
            )
        elif action.action == ACTION_REVIEW:
            controls.append("<span class=\"note\">Review — the project needs a human decision.</span>")
        else:
            controls.append(f"<span class=\"note\">{_esc(action.label)}</span>")
    return " ".join(controls)


def _connection_card(connection):
    """One compact dashboard card: what needs attention, how many issues, and
    where to open it — the contract's own summary line, nothing invented."""
    ref = connection.identity.display_reference or connection.identity.package_id
    chips = [
        label for label in (
            connection.decision_label,
            connection.output.output_label,
            connection.output.verification_label,
        ) if label
    ]
    issues = (
        f'<span class="issues">{len(connection.blockers)} issues</span>'
        if connection.attention.requires_attention else ""
    )
    summary = f'<div class="line">{_esc(connection.summary)}</div>' if connection.summary else ""
    go = "Review →" if connection.attention.requires_attention else "Details →"
    return (
        f'<a class="card connection-card" href="/connections/{_esc_attr(connection.identity.package_id)}">'
        f'<div class="row"><span class="ref">{_esc(ref)}</span>{issues}</div>'
        f"{summary}"
        f'<div class="row"><span class="line">{" ".join(_chip(label) for label in chips)}</span>'
        f'<span class="go">{_esc(go)}</span></div>'
        "</a>"
    )


# --------------------------------------------------------------------------------------
# The two real pages.
# --------------------------------------------------------------------------------------
def render_project_page(view: ProjectReviewView) -> str:
    """Renders the project dashboard from a ProjectReviewView alone: identity,
    the view's own counts, then attention first, completed second."""
    review_cards = "".join(_connection_card(c) for c in view.review_items)
    completed_cards = "".join(_connection_card(c) for c in view.completed_items)
    total = len(view.review_items) + len(view.completed_items)
    counts = view.counts
    body = (
        "<main>"
        "<header class=\"masthead\">"
        "<div class=\"brand\">SteelSpec review</div>"
        f"<h1>{_esc(view.project_id)}</h1>"
        f'<p class="meta">Revision {view.revision} · Status {_chip(view.status_label)}</p>'
        "</header>"
        '<section class="summary">'
        + _stat("connections", total)
        + _stat("need attention", view.requiring_attention, " stat-attention")
        + _stat("verified", view.verified, " stat-verified")
        + "</section>"
        f'<p class="counts-line">Counts: {counts.review} review · {counts.confirmation} confirmation'
        f' · {counts.auto} automated · {counts.blocked} blocked</p>'
        f'<p class="summary-line">{_esc(view.summary)}</p>'
        f'<p class="actions">{_project_action_controls(view.actions)}</p>'
        "<h2>Needs your attention</h2>"
        + (review_cards or '<p class="note">None — nothing needs attention.</p>')
        + "<h2>Completed / verified</h2>"
        + (completed_cards or '<p class="note">None yet.</p>')
        + "</main>"
    )
    return _page("SteelSpec review", body)


def _provenance_rows(provenance):
    """The view's own provenance entries as definition rows, in the view's order."""
    return "".join(
        f"<dt>{_esc(p.field)}</dt>"
        f"<dd>{_esc(p.provenance)} ({_esc(p.provenance_label)})</dd>"
        for p in provenance
    )


def _evidence_block(evidence):
    """The drawing evidence the view carries, printed verbatim."""
    return (
        "<h2>Drawing evidence</h2>"
        f'<div class="card"><p class="evidence">{_esc(evidence.evidence_text)}</p></div>'
    )


def render_connection_detail_page(project_view: ProjectReviewView, connection: ConnectionReviewView) -> str:
    """Renders one connection's review detail page from the view model alone,
    ordered for a human decision: identity, attention, output truth, evidence,
    AI values, provenance, then the resolution tasks and the single submit."""
    ident = connection.identity
    task_by_type = {task.task_type: task.task_id for task in connection.tasks}
    attention = ""
    if connection.attention.requires_attention:
        warning_items = ""
        if connection.warnings:
            warning_items = "".join(
                '<div class="blocker">'
                f'<div class="head"><h3>{_esc(w.title)}</h3>{_chip(w.severity_label)}</div>'
                f"<p>{_esc(w.message)}</p>"
                "</div>"
                for w in connection.warnings
            )
        attention = (
            '<section class="card attention-banner banner">'
            '<div class="banner-label">Needs your attention</div>'
            f'<p class="big">{len(connection.blockers)} items need resolution</p>'
            + _blocker_items(connection.blockers, task_by_type)
            + warning_items
            + "</section>"
        )
    output = _output_section(connection)
    provenance_rows = _provenance_rows(connection.provenance)
    tasks = connection.tasks
    actions = {action.action for action in connection.actions}
    unsupported = unsupported_tasks(connection)
    if ACTION_RESOLVE in actions and not unsupported:
        resolution_area = (
            "<h2>Resolve connection</h2>"
            f'<form method="post" action="/connections/{_esc_attr(ident.package_id)}/resolve">'
            f'<input type="hidden" name="revision" value="{project_view.revision}">'
            f'<ol class="tasks">{_task_sections(tasks)}</ol>'
            '<div class="card">'
            '<label class="field-label" for="evidence">Evidence note (optional)</label>'
            '<textarea id="evidence" name="evidence" '
            'placeholder="Reference the drawing or note what you supplied"></textarea>'
            '<br><br><button class="btn btn-primary">Resolve connection</button>'
            "</div>"
            "</form>"
        )
    elif unsupported:
        resolution_area = (
            "<h2>Actions</h2>"
            '<div class="card"><p class="unsupported">This connection cannot be resolved '
            "from this UI slice: unsupported task answer type(s) "
            f"{', '.join(_esc(t.answer_type) for t in unsupported)}.</p></div>"
        )
    else:
        read_only = (
            "<h2>Resolution tasks</h2>"
            f'<ol class="tasks">{_task_sections(tasks, interactive=False)}</ol>'
            if tasks else ""
        )
        resolution_area = (
            read_only
            + "<h2>Actions</h2>"
            + '<div class="card"><p class="note">No actions available for this connection.</p></div>'
        )
    connection_id = (
        _esc(ident.connection_id) if ident.connection_id
        else '<span class="note">Connection ID not yet supplied</span>'
    )
    body = (
        "<main>"
        '<p class="backlink"><a href="/">&#8592; Back to project</a></p>'
        "<header class=\"masthead\">"
        '<div class="brand">Connection review</div>'
        f"<h1>{_esc(ident.package_id)}</h1>"
        '<dl class="meta-list">'
        f"<dt>Decision</dt><dd>{_chip(connection.decision_label)}</dd>"
        f"<dt>Connection</dt><dd>{connection_id}</dd>"
        f"<dt>Project</dt><dd>{_esc(ident.project_id) if ident.project_id else '&mdash;'}</dd>"
        f"<dt>Revision</dt><dd>{ident.revision}</dd>"
        "</dl>"
        "</header>"
        + f"{attention}"
        + f"{output}"
        + _evidence_block(connection.evidence)
        + "<h2>AI-extracted values</h2>"
        + '<p class="note">Extracted observations from the drawing — not confirmed engineering facts.</p>'
        + f'<div class="card"><dl class="kv">{_extracted_items(connection.extracted)}</dl></div>'
        + "<h2>Provenance</h2>"
        + (f'<div class="card"><dl class="kv">{provenance_rows}</dl></div>' if provenance_rows
           else '<div class="card"><p class="note">None recorded.</p></div>')
        + f"{resolution_area}"
        + "</main>"
    )
    return _page(f"Connection {ident.package_id}", body)


# --------------------------------------------------------------------------------------
# The production connection-review page (J25) — one authorized project's review, composed
# by `app/production_connection_review.py` and handed here as plain data.
#
# This layer escapes and arranges, and decides nothing: the identity, the coverage, the
# recorded state, the queue and the availability of every action all arrive already
# determined. There are no controls on this page and no form of any kind — each action the
# views carry is listed with what the composition says can be done with it, and reviewing
# is the reading the page already provides. The two bands (the reconstruction and the
# recorded state) are rendered by the same atoms, so one cannot drift from the other.
# --------------------------------------------------------------------------------------
def _review_section(connection):
    """One connection, read-only: what it is, why it needs attention, what was read for
    it, and its tasks without inputs. Everything is the view's own — nothing recomputed."""
    ident = connection.identity
    ref = ident.display_reference or ident.package_id
    task_by_type = {task.task_type: task.task_id for task in connection.tasks}
    chips = " ".join(
        _chip(label) for label in (
            connection.decision_label,
            connection.output.output_label,
            connection.output.verification_label,
        ) if label
    )
    provenance_rows = _provenance_rows(connection.provenance)
    tasks = connection.tasks
    return (
        '<section class="card">'
        f'<div class="head"><h3>{_esc(ref)}</h3>{_chip(connection.decision_label)}</div>'
        f'<p class="line">{_esc(connection.summary)}</p>'
        f'<p>{chips}</p>'
        '<dl class="meta-list">'
        f"<dt>Connection</dt><dd>{_esc(ident.connection_id) if ident.connection_id else '&mdash;'}</dd>"
        f"<dt>Project</dt><dd>{_esc(ident.project_id) if ident.project_id else '&mdash;'}</dd>"
        f"<dt>Revision</dt><dd>{ident.revision}</dd>"
        "</dl>"
        + _blocker_items(connection.blockers, task_by_type)
        + _blocker_items(connection.warnings, {})
        + _output_section(connection)
        + _evidence_block(connection.evidence)
        + "<h4>AI-extracted values</h4>"
        + f'<div class="card"><dl class="kv">{_extracted_items(connection.extracted)}</dl></div>'
        + "<h4>Provenance</h4>"
        + (f'<div class="card"><dl class="kv">{provenance_rows}</dl></div>' if provenance_rows
           else '<div class="card"><p class="note">None recorded.</p></div>')
        + ("<h4>Resolution tasks (read-only)</h4>"
           f'<ol class="tasks">{_task_sections(tasks, interactive=False)}</ol>'
           if tasks else "")
        + "</section>"
    )


def _review_sections(view):
    """Every connection of one view, attention first — the view's own two groups, in the
    view's own order within each. This function reorders nothing."""
    parts = []
    for connection in tuple(view.review_items) + tuple(view.completed_items):
        parts.append(_review_section(connection))
    return "".join(parts)


def render_workflow_review_page(view, *, identity=(), coverage=(), capture_runs=(),
                                persisted_code="", persisted_revisions=(), persisted_view=None,
                                recorded_readings=(), limitations=(), refusal_code="",
                                refusal_detail="", action_prefix=""):
    """One project's review as a page: identity, extraction coverage, the reconstructed
    queue, what can and cannot be done, and the recorded state beside it.

    `view` may be absent (the reconstruction refused); the recorded band is rendered
    whatever the reconstruction did, and the absence of recorded state is stated with the
    store's own code rather than filled in. Nothing here writes or reads.

    `recorded_readings` (J81) arrives as `(task, field statement, evidence statement)`
    triples already composed by the production caller, exactly as `limitations` does: the
    two statements are engineering statements, and this layer prints and escapes what it is
    handed without deciding anything about either.
    """
    if view is not None:
        heading = view.project_id
        masthead_meta = (
            f'<p class="meta">Revision {view.revision} · Status {_chip(view.status_label)}</p>'
        )
        queue = (
            "<h2>Connection review</h2>"
            + (_review_sections(view) or '<p class="note">No connection was reconstructed.</p>')
        )
    else:
        heading = persisted_view.project_id if persisted_view is not None else ""
        masthead_meta = '<p class="meta">No reconstruction — see the refusal below.</p>'
        queue = ""
    refusal = (
        '<section class="card attention-banner banner">'
        '<div class="banner-label">Not reconstructed</div>'
        f'<p class="big">{_esc(refusal_code)}</p>'
        f"<p>{_esc(refusal_detail)}</p>"
        "</section>"
    ) if refusal_code else ""
    identity_body = "".join(
        f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>" for label, value in identity
    )
    coverage_body = "".join(
        f"<dt>{_esc(label)}</dt><dd>{_esc(value)}</dd>" for label, value in coverage
    )
    runs = ", ".join(str(run) for run in capture_runs) if capture_runs else "(none)"
    revisions = (
        ", ".join(str(revision) for revision in persisted_revisions)
        if persisted_revisions else "(none)"
    )
    if persisted_view is not None:
        recorded_body = (
            f'<p class="meta">Recorded revision {persisted_view.revision} · Status '
            f'{_chip(persisted_view.status_label)}</p>'
            f'<p class="summary-line">{_esc(persisted_view.summary)}</p>'
            + _review_sections(persisted_view)
        )
    else:
        recorded_body = (
            '<p class="note">No connection-review state is persisted for this project. '
            "Reading this page changes nothing: nothing was created, recorded or advanced.</p>"
        )
    limit_items = "".join(
        '<div class="blocker">'
        f'<div class="head"><h3>{_esc(label)}</h3>{_chip(state)}</div>'
        f"<p>{_esc(reason)}</p>"
        "</div>"
        for label, state, reason in limitations
    )
    # J81: one line per persisted reading. The two statements are printed in full, and the
    # field statement is NOT given a chip: a chip is an availability verdict, and this layer
    # has none to give about a task's own field or about what the evidence establishes.
    reading_items = "".join(
        '<div class="blocker">'
        f'<div class="head"><h3>{_esc(task)}</h3></div>'
        f'<p class="meta">{_esc(field)}</p>'
        f"<p>{_esc(evidence)}</p>"
        "</div>"
        for task, field, evidence in recorded_readings
    )
    body = (
        "<main>"
        + _backlink(action_prefix)
        + "<header class=\"masthead\">"
        + '<div class="brand">SteelSpec production review</div>'
        + f"<h1>{_esc(heading)}</h1>"
        + masthead_meta
        + "</header>"
        + refusal
        + "<h2>Project</h2>"
        + (f'<div class="card"><dl class="kv">{identity_body}</dl></div>' if identity_body
           else '<div class="card"><p class="note">Not stated.</p></div>')
        + "<h2>Extraction coverage</h2>"
        + (f'<div class="card"><dl class="kv">{coverage_body}</dl></div>' if coverage_body
           else '<div class="card"><p class="note">Nothing was reconstructed, so no coverage '
                "is stated.</p></div>")
        + f'<p class="note">Extraction runs: {_esc(runs)}</p>'
        + queue
        + "<h2>Actions</h2>"
        + (f'<div class="card">{limit_items}</div>' if limit_items
           else '<div class="card"><p class="note">No action applies: nothing was '
                "reconstructed and nothing is recorded.</p></div>")
        + "<h2>Recorded review state</h2>"
        + f'<p class="big">{_esc(persisted_code)}</p>'
        + f'<p class="note">Recorded revisions: {_esc(revisions)}</p>'
        + recorded_body
        # J81: the recorded band's evidence half, printed only where there are persisted
        # readings to print. Nothing is stated about a project that recorded none — the
        # absence is already stated above, in the store's own words.
        + (
            "<h2>Recorded field readings</h2>"
            '<p class="note">One line per persisted task: the field the task itself states, '
            "beside the candidate its own item's recorded address establishes. The two are "
            "reported separately and neither is derived from the other.</p>"
            f'<div class="card">{reading_items}</div>'
            if reading_items else ""
        )
        + "</main>"
    )
    return _page(f"Review {heading}" if heading else "Review", body)


# --------------------------------------------------------------------------------------
# The annotation-evidence surface (J29) — the marks J28 read off a page, printed as the
# record states them. This layer prints and escapes; every sentence that says what the
# evidence IS comes from the composition as data, so no engineering claim is made here.
# --------------------------------------------------------------------------------------
_ANNOTATION_EVIDENCE_ABSENT = '<span class="note">(not stated)</span>'

# The recorded fields, in the order the table prints them. They are J28's own names:
# nothing here renames a value, and nothing here adds one.
_ANNOTATION_EVIDENCE_HEADERS = (
    "mark_candidate",
    "mark_readable",
    "annotation_x",
    "annotation_y",
    "operator_count",
    "duplicate_operator_count",
    "schedule_row_candidate",
    "tag_box_present",
    "leader_present",
)


def _annotation_evidence_rows(occurrences):
    """One row per occurrence: the recorded fields, then the reading itself.

    The cells are read in the same order as `_ANNOTATION_EVIDENCE_HEADERS`. A field the
    record does not state is printed as absent rather than as a blank cell, so "the
    record says nothing" never looks like "the record says nothing here either".
    """
    return "".join(
        "<tr>"
        + "".join(
            f"<td>{_esc(cell) if cell is not None else _ANNOTATION_EVIDENCE_ABSENT}</td>"
            for cell in (
                occurrence.mark_candidate,
                occurrence.mark_readable,
                occurrence.annotation_x,
                occurrence.annotation_y,
                occurrence.operator_count,
                occurrence.duplicate_operator_count,
                occurrence.schedule_row_candidate,
                occurrence.tag_box_present,
                occurrence.leader_present,
            )
        )
        + "<td>"
        + "<details><summary>record</summary>"
        + f"<pre>{_esc(occurrence.evidence_json)}</pre>"
        + f"<pre>{_esc(occurrence.rule_set_json)}</pre>"
        + "</details>"
        + f'<p class="note">version {_esc(occurrence.extractor_version)} ·'
        + f' read {_esc(occurrence.extracted_at)}</p>'
        + f'<p class="note">page sha256 {_esc(occurrence.source_pdf_sha256)}</p>'
        + "</td>"
        + "</tr>"
        for occurrence in occurrences
    )


def render_annotation_evidence_page(review, *, action_prefix=""):
    """One project's recorded PDF annotation evidence, as a page.

    Everything printed is the record's own, under the name it was recorded with, and a
    field the record does not state is printed as absent. The page selector is links
    only: this surface has no input, no form and no control that could change anything.
    """
    boundary = "".join(f"<p>{_esc(sentence)}</p>" for sentence in review.boundary)
    showing = (
        f"page {_esc(review.page_filter)}" if review.page_filter is not None else "every page"
    )
    state = (
        '<section class="card attention-banner banner">'
        '<div class="banner-label">Annotation evidence</div>'
        f'<p class="big">{_esc(review.state_code)}</p>'
        f"<p>{_esc(review.state_detail)}</p>"
        "</section>"
    )
    if review.pages:
        selector = '<p><a href="annotations">All pages</a> · ' + " ".join(
            f'<a href="?page={page}">{_esc(page)}</a>' for page in review.pages
        ) + "</p>"
    else:
        selector = '<p class="note">No page of this project has recorded evidence.</p>'
    stats = (
        _stat("Recorded occurrences", review.total_occurrences)
        + _stat("Shown", review.shown_occurrences)
        + _stat("Pages with evidence", len(review.pages))
        + _stat("Drawings shown", len(review.groups))
    )
    groups = "".join(
        "<section>"
        f'<h2>Drawing {_esc(group.drawing_id)} · page {_esc(group.page_number)}</h2>'
        f'<p class="note">{_esc(len(group.occurrences))} recorded occurrence(s) stand '
        "for this page.</p>"
        '<table class="evidence"><thead><tr>'
        + "".join(f"<th>{_esc(header)}</th>" for header in _ANNOTATION_EVIDENCE_HEADERS)
        + "<th>record</th></tr></thead><tbody>"
        + _annotation_evidence_rows(group.occurrences)
        + "</tbody></table>"
        + "</section>"
        for group in review.groups
    )
    body = (
        "<main>"
        + _backlink(action_prefix)
        + '<header class="masthead">'
        + '<div class="brand">SteelSpec production review</div>'
        + f"<h1>{_esc(review.project_id)}</h1>"
        + f'<p class="meta">PDF annotation evidence · showing {showing}</p>'
        + "</header>"
        + state
        + "<h2>What this page is</h2>"
        + f'<div class="card">{boundary}</div>'
        + "<h2>Recorded</h2>"
        + f'<div class="summary">{stats}</div>'
        + "<h2>Pages</h2>"
        + f'<div class="card">{selector}</div>'
        + "<h2>Occurrences</h2>"
        + (groups or '<p class="note">No occurrence is shown.</p>')
        + "</main>"
    )
    return _page(f"Annotation evidence {review.project_id}", body)


# --------------------------------------------------------------------------------------
# The page-exception surface (J18) — the pages of one drawing set whose response
# could not be read, and the one action a human may take on each of them.
#
# The record's own two lines are printed verbatim, the record's own refusal code
# is printed when it could not be listed, and a Retry control is rendered ONLY on
# a page whose own view carries the retry action. Nothing here reads, retries or
# decides: rendering this page is not an action, and no control exists that the
# view did not carry for that exact page.
# --------------------------------------------------------------------------------------
def _backlink(action_prefix: str) -> str:
    """The surface's own back link.

    Empty prefix -> `/`, which is what the internal review UI's root route is and
    what every page here has always rendered. A prefix names the surface's own
    root, so a page served from a mounted production path links back into that
    path instead of to a route it does not own.
    """
    return f'<p class="backlink"><a href="{_esc(action_prefix) or "/"}">&#8592; Back to project</a></p>'


def _page_exception_notice(notice):
    """The result of the retry a human just requested, as the view reported it.

    The tone comes from J17's own `resolved`: green only when J17 said the page
    stopped being a failure, red when it said the page still is, neutral when it
    said nothing at all — which covers both a refusal and a request that was only
    accepted. The state is always printed, so a refusal reads as the code it was
    refused with, and a request that has not been read yet reads as exactly that.
    """
    if notice is None:
        return ""
    if notice.resolved is True:
        tone = "success-banner"
    elif notice.resolved is False:
        tone = "fail-banner"
    else:
        tone = "attention-banner"
    page = (
        f'<p class="note">Page: {notice.page_number}</p>'
        if notice.page_number is not None else ""
    )
    return (
        f'<section class="card {tone} banner">'
        '<div class="banner-label">Retry result</div>'
        f'<p class="big">{_esc(notice.state_label)}</p>'
        f"<p>{_esc(notice.detail)}</p>"
        f'<p class="note">Result state: {_esc(notice.state)}</p>'
        f"{page}"
        "</section>"
    )


def _page_exception_card(exception, action_prefix=""):
    """One unread page: what it is, what state it is in, why — and the Retry
    control its own view offers, named for that page.

    `action_prefix` is where THIS surface is mounted (J19). It is empty for the
    internal review UI, whose own routes are the ones the controls name, and it
    is the production review surface's own path when the same page is served
    from the authenticated production route — so the Retry control posts to the
    surface that rendered it rather than to a route that does not exist there.
    """
    controls = []
    for action in exception.actions:
        if action.action == ACTION_RETRY_PAGE:
            controls.append(
                f'<form method="post" action="{_esc(action_prefix)}/pages/{exception.page_number}/retry">'
                f'<button class="btn btn-primary">{_esc(action.label)}</button></form>'
            )
        else:
            controls.append(f'<span class="note">{_esc(action.label)}</span>')
    return (
        '<section class="card attention-banner banner">'
        f'<div class="banner-label">Unread page {exception.page_number}</div>'
        f"<h3>{_esc(exception.type_label)}</h3>"
        f"<p>{_chip(exception.state_label)}</p>"
        f"<p>{_esc(exception.detail)}</p>"
        f'<p class="note">State: {_esc(exception.state)} · '
        f"Type: {_esc(exception.exception_type)}</p>"
        + (
            f'<p class="actions">{" ".join(controls)}</p>' if controls
            else '<p class="note">No action is available for this page right now.</p>'
        )
        + "</section>"
    )


def render_page_exceptions_page(view: PageExceptionListView, *, action_prefix: str = "") -> str:
    """Renders the page-exception surface from a PageExceptionListView alone:
    the project and the status it carries, what the record states, and per unread
    page the one action the view offers for that page.

    `action_prefix` says where this surface is mounted, so the page's one control
    and its back link name the surface that rendered it. It is empty for the
    internal review UI (whose own routes are named) and the production review
    surface's own path when the authenticated production route serves the same
    page. Nothing else about the page changes: the view is rendered exactly as
    the view model states it either way.
    """
    refusal = ""
    if view.record_refusal is not None:
        refusal = (
            '<section class="card fail-banner banner">'
            '<div class="banner-label">Record not listable</div>'
            f'<p class="big">{_esc(view.record_refusal)}</p>'
            + (f"<p>{_esc(view.record_refusal_message)}</p>"
               if view.record_refusal_message else "")
            + "</section>"
        )
    if view.exceptions:
        listing = "".join(_page_exception_card(e, action_prefix) for e in view.exceptions)
    elif view.record_refusal is None:
        # The record states no parse failure, and it states it — so the empty
        # list is a fact about the drawing set, not a missing answer.
        listing = (
            '<div class="card"><p class="note">No page of this drawing set is recorded as '
            "having failed to parse.</p></div>"
        )
    else:
        # A record that could not be listed states no page count and no page list;
        # showing an empty list here would present half of a contradiction as one.
        listing = ""
    meta = (
        f"Status {_chip(view.status_label)} · record not listable"
        if view.record_refusal is not None
        else f"Status {_chip(view.status_label)} · {len(view.exceptions)} unread page(s)"
    )
    body = (
        "<main>"
        f"{_backlink(action_prefix)}"
        '<header class="masthead">'
        '<div class="brand">Page exceptions</div>'
        f"<h1>{_esc(view.project_id or 'Unknown project')}</h1>"
        f'<p class="meta">{meta}</p>'
        "</header>"
        '<p class="note">Reading a page again is a request a human makes. Nothing on this page '
        "reads the drawing set, retries anything or decides anything by itself.</p>"
        + _page_exception_notice(view.notice)
        + refusal
        + (f"<h2>Unread pages</h2>{listing}" if listing else "")
        + "<h2>The record this list is read from</h2>"
        '<div class="card"><dl class="kv">'
        f"<dt>Coverage record</dt><dd>{_esc(view.coverage_line) or '&mdash;'}</dd>"
        f"<dt>Parse failures</dt><dd>{_esc(view.failures_line) or '&mdash;'}</dd>"
        "</dl></div>"
        f'<p class="note">{_esc(view.summary)}</p>'
        "</main>"
    )
    return _page("Page exceptions", body)


# --------------------------------------------------------------------------------------
# Message pages — stale refusals, refusals, unbound sessions.
# --------------------------------------------------------------------------------------
def render_message_page(
    title: str, message: str, *, refresh_form: bool = False, action_prefix: str = "",
) -> str:
    """A page that states one thing and offers one control, if any.

    `action_prefix` is the surface this page was served from (J19), exactly as
    for the page-exception surface: empty means the internal review UI's own
    routes, and a prefix means the mounted production surface's.
    """
    refresh = (
        f'<form method="post" action="{_esc(action_prefix)}/refresh">'
        '<button class="btn">Refresh review</button></form>'
        if refresh_form else ""
    )
    return _page(
        title,
        "<main class=\"msg-page\">"
        f"{_backlink(action_prefix)}"
        '<div class="card">'
        f"<h1>{_esc(title)}</h1><p>{_esc(message)}</p>{refresh}"
        "</div>"
        "</main>",
    )


def render_unbound_page() -> str:
    return _page(
        "SteelSpec review",
        "<main>"
        "<header class=\"masthead\">"
        '<div class="brand">SteelSpec review</div>'
        "<h1>Project review</h1>"
        "</header>"
        '<div class="card">'
        "<p>No review workflow is bound to this server session. Bind one through "
        "the existing workflow API to review it here.</p>"
        "</div>"
        "</main>",
    )
