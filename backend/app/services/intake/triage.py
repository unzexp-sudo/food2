"""Gate 1 — "is this message an order at all?"

Gate 2 (see `app/ai/pipeline.py`) makes a human review every extraction because
a mis-read order is expensive. Gate 1 exists for a cheaper reason: reviewing
junk is also expensive. "你好", "收到", "谢谢老板" and sticker-only messages are
not orders, and every one of them currently lands in the intake inbox as a
document awaiting review. With mandatory review on, that means a person has to
look at each one to say "this is nothing" — the queue drowns and the real
orders get buried.

So: classify first. Only plausible orders become intake documents.

Design
------
Tier 0 — deterministic. Empty, emoji-only, greetings, acknowledgements,
    system events, and a question with nothing ordered in it ("where is this
    account??", "多少钱一斤？"). Also: an attachment is treated as an order,
    because the customer's habit is "photo the handwritten list and send it"
    and 95% of attachments really are orders. They still get reviewed; triage
    is not a substitute for Gate 2.

    Tier 0 is the ONLY tier that is ever parked, so anything added here must be
    safe to hide from a human. That is why a question is only Tier 0 when it
    carries no quantity, no digit and no order verb: "能送点土豆过来吗？" is an
    order wearing a question mark and must stay in the inbox.

Tier 1 — Chinese heuristics. Quantities with units (50斤 / 2箱 / 10公斤) are
    the strongest signal; order verbs (要/订/来/送/补), multiple lines and
    delivery-time mentions add weight. Score against a threshold.
    **A Tier 1 "not_order" is never parked** — the score cannot tell
    "几点送？" (not an order) from "能送点土豆过来吗？" (an order); both are -1.
    The park rule is `is_parkable` below, and it is the only copy of it.

Tier 2 — LLM, for whatever lands in "unclear". Not built yet; unclear
    currently falls through to "order", because dropping a real order is far
    worse than showing an extra row.

Tier 3 — human promote. A parked message can always be pushed into the intake
    inbox by hand, so a mis-classification is recoverable, never fatal.

Rollout is gated by `settings.intake_triage_mode`:
    "off"     — classify nothing, behave exactly as before
    "shadow"  — classify and record, change no behaviour (default first)
    "enforce" — non-orders are parked instead of creating an intake job
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("erp.intake.triage")

ORDER = "order"
NOT_ORDER = "not_order"
UNCLEAR = "unclear"

# --- Tier 0: things that are definitely not orders ----------------------------

# Greetings and openers. Whole-message match only — "你好" inside a longer
# order ("你好，明天要 50斤土豆") must NOT disqualify it.
_GREETINGS = [
    "你好", "您好", "大家好", "早上好", "中午好", "下午好", "晚上好",
    "hi", "hello", "hey", "在吗", "在么", "在不", "有人在吗", "老板在吗",
]

# Acknowledgements / closers / politeness.
_ACKS = [
    "好的", "好", "收到", "收到了", "谢谢", "感谢", "多谢", "辛苦了", "辛苦",
    "明白", "知道了", "没问题", "可以", "行", "嗯", "嗯嗯", "哦", "ok",
    "okay", "好的呢", "好的收到", "谢谢老板", "感谢老板", "不好意思",
    "麻烦了", "抱歉", "对不起", "稍等", "等一下", "马上", "好的谢谢",
]

# WeCom system / group events that arrive as ordinary text.
_SYSTEM = [
    "群公告", "已加入群聊", "加入群聊", "邀请", "撤回了一条消息", "系统消息",
    "该消息已撤回", "以上是打招呼的内容", "你已添加了", "现在可以开始聊天",
    "[收到一条新消息]", "拍了拍", "红包", "转账",
]

# Question particles — a genuine question is not an order, but only when
# nothing order-like is present (see _looks_like_question).
_QUESTION_WORDS = ["几点", "什么时候", "多少钱", "怎么卖", "有没有", "到了吗",
                   "到了没", "什么价", "能否", "可否"]

# --- Tier 1: things that signal an order --------------------------------------

# A number glued to a unit is the single strongest signal in this domain.
_UNITS = (
    "斤|公斤|千克|kg|KG|g|克|吨|箱|件|袋|包|瓶|盒|个|只|把|扎|捆|条|块|"
    "根|头|扇|份|支|罐|桶|篮|筐|盘|对|双|串|打|令|卷|米|升|ml|L"
)
# Note: no trailing \b — `\b?` is invalid (a zero-width assertion cannot be
# quantified), and it is not needed here since units are CJK or alphabetic.
_QTY_UNIT_RE = re.compile(rf"(\d+(?:\.\d+)?)\s*(?:到|-|~)?\s*(?:{_UNITS})", re.IGNORECASE)

# Bare numbers (a customer may write "土豆 50" and omit the unit).
_BARE_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

_ORDER_VERBS = [
    "下单", "订购", "訂購", "订货", "訂貨", "采购", "采購", "购买", "補貨",
    "补货", "要", "訂", "订", "来", "來", "送", "拿", "加", "配", "安排",
    "麻烦", "麻煩", "请送", "請送", "需要", "帮我", "幫我", "发", "發",
]

# Delivery timing — "明天早上送到", "今天下午要".
_TIMING = ["明天", "今天", "后天", "後天", "早上", "上午", "中午", "下午",
           "晚上", "之前", "送到", "配送", "交货", "交貨", "到货", "到貨"]

# Emoji / pictograph ranges + WeCom sticker markers.
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "️"
    "]",
    flags=re.UNICODE,
)
# Zero-width and whitespace used to hide content.
_BLANK_RE = re.compile(r"^[\s​-‍﻿]*$")


@dataclass
class TriageVerdict:
    """What triage concluded about one inbound message."""

    decision: str
    score: int = 0
    tier: str = "rules"
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def is_order(self) -> bool:
        # "unclear" is treated as an order on purpose: a false negative drops
        # real revenue, a false positive only costs a reviewer one glance.
        return self.decision in (ORDER, UNCLEAR)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "score": self.score,
            "tier": self.tier,
            "reasons": self.reasons,
            "signals": self.signals,
        }


# --- The park rule, in exactly one place --------------------------------------
#
# "Which verdicts does Gate 1 hide?" was previously written out three times: in
# the ingest path, in the backfill, and in `/triage-report`. Two of them checked
# the tier and one did not, so the report advertised a `tier1` non-order as
# "would be parked" when the gate would never touch it. That matters because the
# report is the safety instrument: its whole purpose is to be eyeballed for
# false positives before trusting enforcement, and a row that can never be
# parked sitting in that list reads as a rule that is too aggressive.
#
# A rule copied into three places will drift. This is the one copy.
PARKABLE_TIER = "tier0"


def is_parkable(verdict: "TriageVerdict | dict[str, Any] | None") -> bool:
    """Would Gate 1 hide this message? Accepts a live verdict or a stored one.

    Both shapes are needed: the ingest path and the backfill hold a
    `TriageVerdict`, while the report reads the dict stored in
    `document_meta["wecom"]["triage"]`.

    Deliberately does NOT consider the mode. `shadow` is about whether the rule
    is applied, not about what the rule is, and the report has to be able to
    describe the rule while it is off.
    """
    if verdict is None:
        return False
    if isinstance(verdict, dict):
        decision = verdict.get("decision")
        tier = verdict.get("tier")
    else:
        decision = getattr(verdict, "decision", None)
        tier = getattr(verdict, "tier", None)
    return decision == NOT_ORDER and tier == PARKABLE_TIER


def _normalize(text: str | None) -> str:
    """Full-width → half-width for digits/latin, collapse whitespace."""
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        # Full-width forms (！-～) → ASCII.
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif ch == "\u3000":  # ideographic space
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    return re.sub(r"[ \t]+", " ", s).strip()


def _strip_emoji(text: str) -> str:
    without = _EMOJI_RE.sub("", text)
    # Drop variation selectors / ZWJ left behind.
    return re.sub(r"[\u200b-\u200d\ufeff]", "", without).strip()


def _is_pictograph_only(text: str) -> bool:
    """True when the message is emoji/sticker/symbol with no real content."""
    if not text:
        return True
    meaningful = ""
    for ch in text:
        if _EMOJI_RE.match(ch):
            continue
        cat = unicodedata.category(ch)
        if cat in ("So", "Sk", "Cf"):  # symbols / formatting
            continue
        meaningful += ch
    return _strip_emoji(meaningful) == ""


def _looks_like_question(text: str) -> bool:
    lowered = text.lower()
    if "?" in lowered or "？" in lowered:
        return True
    return any(w in lowered for w in _QUESTION_WORDS)


def classify_message(
    *,
    text: str | None = None,
    msgtype: str | None = None,
    has_attachment: bool = False,
    filename: str | None = None,
) -> TriageVerdict:
    """Decide whether an inbound message should become an intake document.

    Args:
        text: the message body (may be None for a bare attachment).
        msgtype: WeCom msgtype ("text", "image", "file", "voice", "mixed").
        has_attachment: a file/image was actually resolved and will be parsed.
        filename: original filename, if any.

    Returns:
        A TriageVerdict. Never raises — a triage bug must not drop a message,
        so any unexpected failure returns "unclear" (treated as an order).
    """
    try:
        return _classify(
            text=text, msgtype=msgtype,
            has_attachment=has_attachment, filename=filename,
        )
    except Exception:  # noqa: BLE001 — triage must never break ingestion
        logger.exception("triage failed; defaulting to unclear (treated as order)")
        return TriageVerdict(
            decision=UNCLEAR, score=0, tier="error",
            reasons=["triage raised; defaulted to order"],
        )


def _classify(
    *,
    text: str | None,
    msgtype: str | None,
    has_attachment: bool,
    filename: str | None,
) -> TriageVerdict:
    reasons: list[str] = []
    signals: dict[str, Any] = {}

    body = _normalize(text)
    # An image of a handwritten list carries no text — that is normal, not
    # a reason to reject it.
    text_body = _strip_emoji(body)
    signals["has_text"] = bool(text_body)
    signals["has_attachment"] = bool(has_attachment)
    signals["msgtype"] = msgtype or "text"

    # --- Tier 0a: nothing at all ------------------------------------------
    if not text_body and not has_attachment:
        return TriageVerdict(
            decision=NOT_ORDER, score=0, tier="tier0",
            reasons=["empty or emoji-only message"], signals=signals,
        )

    # --- Tier 0b: attachment → order ---------------------------------------
    # The customer's habit is to photograph the handwritten order. 95% of
    # attachments are orders, and they must be parsed anyway, so there is no
    # saving in gating them. They still stop at Gate 2 for human review.
    if has_attachment:
        reasons.append(
            f"attachment ({filename or msgtype or 'file'}) — orders are usually sent as files/images"
        )
        return TriageVerdict(
            decision=ORDER, score=10, tier="tier0", reasons=reasons, signals=signals,
        )

    # Everything below is text-only.
    lowered = text_body.lower()
    single_line = lowered.replace("\n", " ")
    signals["length"] = len(text_body)
    signals["line_count"] = len([ln for ln in text_body.splitlines() if ln.strip()])

    # --- Tier 0c: whole-message greeting / ack / system event ---------------
    # Matched against the whole (single-line) body so a greeting that merely
    # *opens* an order does not disqualify it.
    compact = re.sub(r"[\s，。！!,.、~～]+", "", single_line)
    for label, words in (("greeting", _GREETINGS), ("acknowledgement", _ACKS),
                         ("system event", _SYSTEM)):
        for w in words:
            if compact == re.sub(r"[\s，。！!,.、~～]+", "", w.lower()):
                return TriageVerdict(
                    decision=NOT_ORDER, score=0, tier="tier0",
                    reasons=[f"{label} only ({w})"], signals=signals,
                )

    if _is_pictograph_only(text_body):
        return TriageVerdict(
            decision=NOT_ORDER, score=0, tier="tier0",
            reasons=["emoji/sticker only"], signals=signals,
        )

    # --- Tier 1: score the order signals -----------------------------------
    score = 0

    qty_matches = _QTY_UNIT_RE.findall(text_body)

    # A greeting or ack that merely *opens* a message must not sink a real
    # order ("你好，明天要 50斤土豆"), so this only fires when nothing
    # order-like follows: no quantity, no digits at all.
    if not qty_matches and not _BARE_NUMBER_RE.search(text_body):
        for label, words in (("greeting", _GREETINGS), ("acknowledgement", _ACKS)):
            for w in words:
                wc = re.sub(r"[\s，。！!,.、~～]+", "", w.lower())
                if wc and compact.startswith(wc):
                    return TriageVerdict(
                        decision=NOT_ORDER, score=0, tier="tier0",
                        reasons=[f"{label} opens the message, nothing ordered ({w})"],
                        signals=signals,
                    )

    verbs = [v for v in _ORDER_VERBS if v in lowered]

    # --- Tier 0d: a message that is ONLY a question -------------------------
    # "where is this account??", "多少钱一斤？", "货到了吗". A customer asking
    # something is not placing an order, and that judgement is deterministic —
    # so it belongs in Tier 0, the only tier that is ever parked.
    #
    # It used to be a mere -3 penalty in the Tier 1 score. The consequence was
    # the worst of both worlds: the message was correctly *classified* as
    # not_order and then still handed to the intake inbox, because Tier 1 is
    # never enforced. A question therefore reached a human either way, which is
    # the exact cost Gate 1 exists to remove.
    #
    # Each exclusion below is load-bearing; together they are the safety
    # argument for parking a question at all:
    #   * a quantity+unit is an order however the sentence is punctuated —
    #     "明天几点能送到？要 50斤土豆" must survive;
    #   * a bare digit is enough to deserve a human glance;
    #   * an order verb is how a Chinese order is phrased as a request —
    #     "能送点土豆过来吗？" is an order, not an enquiry. Without this the
    #     rule would quietly drop real orders that happen to end in a question
    #     mark, which is a lost sale rather than a saved glance.
    if (
        _looks_like_question(text_body)
        and not qty_matches
        and not _BARE_NUMBER_RE.search(text_body)
        and not verbs
    ):
        return TriageVerdict(
            decision=NOT_ORDER, score=0, tier="tier0",
            reasons=["a question with nothing ordered — not an order"],
            signals=signals,
        )

    if qty_matches:
        # Strongest signal in this domain, and a single one must be enough:
        # "土豆 50斤" is a perfectly good order. First match +4, each further
        # match +2, capped at +8.
        add = min(4 + (len(qty_matches) - 1) * 2, 8)
        score += add
        reasons.append(f"quantity+unit × {len(qty_matches)} ({', '.join(qty_matches[:4])})")
        signals["qty_units"] = qty_matches[:10]

    bare_numbers = _BARE_NUMBER_RE.findall(text_body)
    if bare_numbers and not qty_matches:
        score += 2
        reasons.append(f"numbers but no unit ({', '.join(bare_numbers[:4])})")
        signals["bare_numbers"] = bare_numbers[:10]
    elif bare_numbers:
        # Digits alongside a unit add a little corroboration.
        score += 1

    verbs = [v for v in _ORDER_VERBS if v in lowered]
    if verbs:
        score += 2
        reasons.append(f"order verbs: {', '.join(verbs[:4])}")
        signals["verbs"] = verbs[:10]

    line_count = signals["line_count"]
    if line_count >= 3:
        score += 3
        reasons.append(f"{line_count} lines — reads like a list")
    elif line_count == 2:
        score += 2
        reasons.append("2 lines — reads like a list")

    timings = [t for t in _TIMING if t in lowered]
    if timings:
        score += 1
        reasons.append(f"delivery timing: {', '.join(timings[:4])}")
        signals["timing"] = timings[:10]

    # A pure question ("几点送?") is not an order unless it also carries
    # quantities — "明天要 50斤土豆，几点能送到?" is both.
    if _looks_like_question(text_body) and not qty_matches:
        # "几点送？" carries the verb 送 and a timing word, so a small penalty
        # would still leave it looking like an order. -3 clears those.
        score -= 3
        reasons.append("question with no quantity — not an order")
        signals["question"] = True

    signals["score"] = score

    if score >= 4:
        return TriageVerdict(
            decision=ORDER, score=score, tier="tier1", reasons=reasons, signals=signals,
        )
    if score <= 0:
        return TriageVerdict(
            decision=NOT_ORDER, score=score, tier="tier1", reasons=reasons, signals=signals,
        )
    return TriageVerdict(
        decision=UNCLEAR, score=score, tier="tier1", reasons=reasons, signals=signals,
    )
