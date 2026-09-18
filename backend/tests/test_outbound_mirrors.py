"""The outbound log's filter lists must match the enums they mirror.

`WeComOutboundPage.tsx` carries two hand-written arrays — the templates and the
statuses the operator can filter the send log by — and neither is derived from
anything. The templates come from this service's own `notify()` call sites; the
statuses come from the gateway's `OutboundStatus` literal in the other repo.

They drifted. The page listed six templates while the ERP sends seven, and it
listed five statuses while the gateway can report six, so `blocked` — the status
a guard refusal produces, and the one production was actually in — could not be
filtered to. Nothing failed. The row was on screen; the tool for finding it was
not.

A test is the only thing that can hold two lists in step, so these tests read
both trees. That is unusual, and deliberate: the two halves of this defect live
in the same repository, so a single test can see both. The third leg — the
gateway's `TemplateName` and `OutboundStatus` in `WeCom1/app/schemas/wecom.py` —
is not in this repository and cannot be read here, so it is pinned as a literal
below and recorded in `docs/WECOM_CONTRACTS.md`. If the gateway changes, the
pin is what has to move, and that is the point: it moves visibly.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTIFY_MODULE = REPO_ROOT / "backend" / "app" / "services" / "notify" / "wecom_notify.py"
OUTBOUND_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "wecom" / "WeComOutboundPage.tsx"
I18N_EN = REPO_ROOT / "frontend" / "src" / "i18n" / "en.ts"
I18N_ZH = REPO_ROOT / "frontend" / "src" / "i18n" / "zh.ts"

# Mirrors `TemplateName` in WeCom1/app/schemas/wecom.py. The gateway rejects any
# template outside this set, so it is a hard boundary, not a preference.
GATEWAY_TEMPLATES = {
    "order_confirmed",
    "needs_customer_confirm",
    "parse_failed",
    "intake_needs_review",
    "out_for_delivery",
    "delivered",
    "invoice_ready",
}

# Mirrors `OutboundStatus` in WeCom1/app/schemas/wecom.py.
GATEWAY_OUTBOUND_STATUSES = {
    "sent",
    "mock",
    "skipped",
    "failed",
    "pending",
    "blocked",
}


def _notify_templates() -> set[str]:
    """Every template this service passes to `notify()`, read from the source.

    Scanned rather than driven through the handlers on purpose: a handler that
    is never reached in a test would hide its template, and the question here is
    what the code *can* send, not what one test path happened to send.
    """
    tree = ast.parse(NOTIFY_MODULE.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "notify":
            continue
        for kw in node.keywords:
            if kw.arg == "template" and isinstance(kw.value, ast.Constant):
                found.add(str(kw.value.value))
    return found


def _ts_string_array(const_name: str) -> set[str]:
    """Pull one `const NAME = ["a", "b"]` array out of the outbound page."""
    src = OUTBOUND_PAGE.read_text(encoding="utf-8")
    match = re.search(
        rf"const {const_name}\s*(?::[^=]+)?=\s*\[(.*?)\]", src, re.S
    )
    assert match, f"{const_name} is no longer a literal array in WeComOutboundPage.tsx"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.fixture(scope="module", autouse=True)
def _both_trees_present() -> None:
    """Skip where the frontend source is not shipped alongside the backend.

    The production image copies only the compiled SPA, so these tests cannot run
    there. They run in development and in CI, which is where a mirror drifts.
    """
    missing = [p for p in (NOTIFY_MODULE, OUTBOUND_PAGE, I18N_EN, I18N_ZH) if not p.exists()]
    if missing:
        pytest.skip(f"not a full checkout: {[str(p.relative_to(REPO_ROOT)) for p in missing]}")


# ---------------------------------------------------------------------------
# The ERP -> gateway boundary
# ---------------------------------------------------------------------------

def test_every_template_the_erp_sends_is_one_the_gateway_accepts():
    """A template outside `TemplateName` is a 422, and the notification is lost."""
    sent = _notify_templates()
    assert sent, "no notify() call sites found — the scan broke, not the code"
    assert sent <= GATEWAY_TEMPLATES, (
        f"the ERP sends {sorted(sent - GATEWAY_TEMPLATES)}, which the gateway "
        "will reject"
    )


def test_the_erp_still_sends_the_whole_pinned_set():
    """Pinned so that adding or dropping a template is a decision, not a diff.

    This is the half of the guard the UI cannot provide: a new call site here
    fails this test, which is what makes someone go and add the filter entry,
    the label and the contract line.
    """
    assert _notify_templates() == GATEWAY_TEMPLATES


# ---------------------------------------------------------------------------
# The mirrors in the UI
# ---------------------------------------------------------------------------

def test_the_send_log_can_be_filtered_by_every_template_the_erp_sends():
    """The defect this module exists for.

    `intake_needs_review` was missing, so the internal "an order is waiting for
    a human" ping — which is written to this very log — had no filter entry.
    """
    filterable = _ts_string_array("TEMPLATES")
    assert _notify_templates() <= filterable, (
        f"the ERP sends {sorted(_notify_templates() - filterable)} but the "
        "outbound log cannot filter to it"
    )


def test_the_send_log_can_be_filtered_by_every_status_the_gateway_reports():
    """`blocked` was missing, and `blocked` is what a guard refusal looks like.

    While `WECOM_SEND_ALLOWLIST` is in force it is the expected status of every
    real customer notification, so an operator could not select the rows that
    were the entire problem.
    """
    filterable = _ts_string_array("OUTBOUND_STATUSES")
    assert filterable == GATEWAY_OUTBOUND_STATUSES, (
        f"missing from the filter: {sorted(GATEWAY_OUTBOUND_STATUSES - filterable)}; "
        f"not a gateway status: {sorted(filterable - GATEWAY_OUTBOUND_STATUSES)}"
    )


def test_every_filterable_template_and_status_has_a_label_in_both_languages():
    """An untranslated entry renders its own i18n key as the label.

    i18next returns the key when a translation is missing, so the dropdown would
    offer `pages.wecom.outbound.template_intake_needs_review` as an option. The
    page passes a `defaultValue` so a drift is legible rather than garbled, but
    legible is not translated.
    """
    missing: list[str] = []
    for path, lang in ((I18N_EN, "en"), (I18N_ZH, "zh")):
        src = path.read_text(encoding="utf-8")

        # Scoped to the `wecomOutbound` block: `sent`, `pending` and `failed`
        # all appear in other status families, so an unscoped search would pass
        # on a label that belongs to a different domain.
        block = re.search(r"wecomOutbound:\s*\{(.*?)\}", src, re.S)
        assert block, f"{path.name} no longer has a status.wecomOutbound block"
        labels = block.group(1)

        for template in _ts_string_array("TEMPLATES"):
            if f"template_{template}:" not in src:
                missing.append(f"{lang} template_{template}")
        for status in _ts_string_array("OUTBOUND_STATUSES"):
            if f"{status}:" not in labels:
                missing.append(f"{lang} status.wecomOutbound.{status}")

    assert not missing, f"no label for {missing}"
