"""Deterministic clean-up of model replies before they are saved and spoken."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

_QUESTION_MARK = re.compile(r"[?？]")


def limit_questions(text: str, max_questions: int = 1) -> str:
    """Cut a reply after its first question if it asks more than `max_questions`.

    The persona prompt asks for one question per turn, but the model often splits an
    "A or B?" question in two (especially in Chinese). A spoken reply that asks two
    things is hard for an older listener to follow, so the rule is enforced in code.
    """
    marks = list(_QUESTION_MARK.finditer(text))
    if len(marks) <= max_questions:
        return text
    return text[: marks[max_questions - 1].end()].strip()


def tidy_reply(text: str) -> str:
    return limit_questions(text.strip())


# The companion gives no medical advice (companion.txt). The prompt is the first line of
# defence; this catches what slips through: medicines, doses, and treatment suggestions.
_MEDICAL_ADVICE = re.compile(
    "|".join(
        [
            # medicines and supplements by name
            r"\b(?:ibuprofen|acetaminophen|paracetamol|tylenol|advil|motrin|aleve|naproxen|"
            r"aspirin|antibiotics?|antihistamines?|benadryl|melatonin|sleeping pills?|"
            r"painkillers?|pain relievers?|laxatives?|antacids?|tums)\b",
            r"布洛芬|对乙酰氨基酚|扑热息痛|阿司匹林|抗生素|消炎药|止痛药|止疼药|安眠药|褪黑素|"
            r"退烧药|感冒药|泻药|胃药|膏药",
            # doses
            r"\b\d+(?:\.\d+)?\s?(?:mg|milligrams?|ml|milliliters?|mcg|iu)\b",
            r"\d+\s?(?:毫克|毫升|粒)",
            # telling her to take / change medication ("did you take your pills?" is fine)
            r"(?<!you )\b(?:take|try taking|start taking|stop taking|skip|double|increase|reduce|"
            r"lower|raise)\s+(?:your|a|an|some|two|one|the|extra)?\s*(?:dose|pills?|"
            r"tablets?|medicines?|medications?|meds)\b",
            r"(?:建议|可以|最好|不妨|试试)[你您]?(?:先)?(?:吃|服|服用|用|加|减|停)(?:点|些|一下)?(?:药|片)",
            r"(?:停药|加量|减量|多吃一片|少吃一片)",
            # home remedies and treatment suggestions
            r"\b(?:put|apply|use|try)\s+(?:some\s+|an?\s+)?(?:ice|heat|a heating pad|"
            r"a warm compress|a cold compress|an ice pack|a heat pack|cream|ointment)\b",
            r"\b(?:ice|heat) (?:packs?|it)\b",
            r"\b(?:you should|try to|make sure to|be sure to)\s+(?:rest|elevate|stretch|"
            r"drink more|eat more|avoid (?:salt|sugar|caffeine))\b",
            r"热敷|冰敷|多喝热水|多喝水|泡脚|按摩一下|拉伸一下",
            # diagnosis / reassurance about causes
            r"\b(?:it'?s|it is|that'?s|that is|sounds like|might be|could be|probably)\s+"
            r"(?:just\s+)?(?:arthritis|an? (?:infection|virus|cold|flu)|vertigo|"
            r"low blood (?:pressure|sugar)|high blood pressure|dehydration|nothing serious|"
            r"normal at your age|not serious)\b",
            r"(?:可能是|应该是|估计是|大概是)(?:关节炎|感冒|发炎|低血压|高血压|低血糖|缺水|颈椎病)",
            r"(?:没什么大碍|不严重|年纪大了都这样)",
        ]
    ),
    re.IGNORECASE,
)
_SAFE_REPLY = {
    "en": "I'm sorry you're dealing with that. I can't give medical advice, "
    "but your doctor is the right person to ask about it.",
    "zh": "听到你不舒服，我很心疼。我不能给医疗建议，这个最好问问医生。",
}
_EMERGENCY = re.compile(r"911|急救|打120")
_CJK = re.compile(r"[一-鿿]")


def gives_medical_advice(text: str) -> bool:
    return bool(_MEDICAL_ADVICE.search(text))


def guard_medical_advice(text: str) -> str:
    """Replace a reply that gives medical advice with a safe one in the same language.
    Emergency replies (call 911) are never replaced."""
    if _EMERGENCY.search(text) or not gives_medical_advice(text):
        return text
    logger.info("replacing a reply that gave medical advice: %r", text)
    return _SAFE_REPLY["zh" if _CJK.search(text) else "en"]
