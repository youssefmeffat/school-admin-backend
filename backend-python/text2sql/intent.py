"""Fast intent classification for the Text-to-SQL pipeline.

The classifier runs before retrieval and LLM SQL generation.

Categories:
- database_query: proceed with retrieval + SQL generation
- greeting: answer directly
- small_talk: conversational response
- offensive: polite redirect
- unknown: ask for clarification

This module intentionally does NOT use an LLM. Intent detection remains
fast and deterministic because it runs on every incoming message.
"""

import re
from dataclasses import dataclass


# ---------------------------------------------------------------------
# Intent names
# ---------------------------------------------------------------------

DATABASE_QUERY = "database_query"
GREETING = "greeting"
SMALL_TALK = "small_talk"
OFFENSIVE = "offensive"
UNKNOWN = "unknown"


@dataclass
class IntentResult:
    intent: str
    reason: str


# ---------------------------------------------------------------------
# Greeting detection
# ---------------------------------------------------------------------

# Full-message match only.
# This means:
#   "hi"                         -> greeting
#   "Hi, how many employees?"   -> database_query
#   "hello, show me employees"  -> database_query
_GREETING_ONLY_RE = re.compile(
    r"^\s*"
    r"(hi|hello|hey|hiya|yo|howdy|greetings|sup|"
    r"what'?s\s+up|"
    r"good\s+(morning|afternoon|evening)|"
    r"how\s+are\s+you|"
    r"thanks|thank\s+you)"
    r"\s*[!.,?]*\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Small talk
# ---------------------------------------------------------------------

_SMALL_TALK_PATTERNS = [
    r"\bi love you\b",
    r"\bwho (are|made|built|created) you\b",
    r"\bwhat(?:'s| is) your name\b",
    r"\bare you (?:a )?(?:bot|ai|robot|human|real)\b",
    r"\btell me a joke\b",
    r"\bwhat can you do\b",
    r"\bhow (?:old|smart) are you\b",
    r"\bgood (?:bot|job)\b",
    r"\bare you (?:single|married|alive)\b",
]

_SMALL_TALK_RE = re.compile(
    "|".join(_SMALL_TALK_PATTERNS),
    re.IGNORECASE,
)

_SMALL_TALK_ONLY_RE = re.compile(
    # One OR MORE small-talk tokens, e.g. "lol nice", "ok great!" -- a
    # message made up entirely of these reaction words is still pure
    # small talk and must not fall through to SQL generation just because
    # it isn't a single-word match.
    r"^\s*(?:(?:lol|haha+|nice|cool|ok|okay|great|awesome)[!.,]*\s*)+$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------
# Offensive language
# ---------------------------------------------------------------------

# Deliberately conservative. This is a redirect mechanism, not a
# comprehensive profanity detector.
_OFFENSIVE_WORDS = {
    "fuck",
    "fucking",
    "shit",
    "bitch",
    "asshole",
    "bastard",
    "dumbass",
    "moron",
}

_OFFENSIVE_PHRASES_RE = re.compile(
    r"\bshut\s+up\b|\bscrew\s+you\b",
    re.IGNORECASE,
)


class IntentClassifier:
    """Classify a raw user message before schema/SQL work."""

    def classify(self, question: str) -> IntentResult:
        q = (question or "").strip()

        if not q:
            return IntentResult(
                UNKNOWN,
                "empty question",
            )

        # Offensive messages should never reach database processing.
        if self._is_offensive(q):
            return IntentResult(
                OFFENSIVE,
                "offensive language detected",
            )

        # Greeting-only messages are handled directly.
        #
        # A mixed message such as:
        # "Hi, how many employees are there?"
        # does not match because the whole message must be a greeting.
        if _GREETING_ONLY_RE.fullmatch(q):
            return IntentResult(
                GREETING,
                "matched greeting-only pattern",
            )

        # Pure conversational reactions.
        if _SMALL_TALK_ONLY_RE.fullmatch(q):
            return IntentResult(
                SMALL_TALK,
                "matched small-talk-only pattern",
            )

        # Small-talk phrases can occur inside a real database question.
        # Example:
        # "Hey, what can you do with the employee data?"
        #
        # If database language is present, let the SQL pipeline handle it.
        if _SMALL_TALK_RE.search(q):
            if self._contains_database_signal(q):
                return IntentResult(
                    DATABASE_QUERY,
                    "database signal detected despite small-talk phrase",
                )

            return IntentResult(
                SMALL_TALK,
                "matched small-talk pattern",
            )

        # Reject clearly unusable messages.
        if self._looks_unclassifiable(q):
            return IntentResult(
                UNKNOWN,
                "too short/vague to classify",
            )

        # Database-first fallback is intentional.
        #
        # We cannot hardcode domain words such as employee, restaurant,
        # customer, salary, etc. because the pipeline is database-agnostic.
        return IntentResult(
            DATABASE_QUERY,
            "default: treated as a database question",
        )

    @staticmethod
    def _is_offensive(q: str) -> bool:
        if _OFFENSIVE_PHRASES_RE.search(q):
            return True

        words = set(
            re.findall(
                r"[a-z']+",
                q.lower(),
            )
        )

        return bool(words & _OFFENSIVE_WORDS)

    @staticmethod
    def _contains_database_signal(q: str) -> bool:
        """Detect generic language strongly suggesting a data question."""

        database_patterns = (
            r"\bhow many\b",
            r"\bcount\b",
            r"\baverage\b",
            r"\bavg\b",
            r"\bsum\b",
            r"\btotal\b",
            r"\bmaximum\b",
            r"\bminimum\b",
            r"\bmax\b",
            r"\bmin\b",
            r"\bhighest\b",
            r"\blowest\b",
            r"\blist\b",
            r"\bshow\b",
            r"\bfind\b",
            r"\bsearch\b",
            r"\bwhere\b",
            r"\bwhich\b",
            r"\bhow much\b",
            r"\btop\b",
        )

        return any(
            re.search(pattern, q, re.IGNORECASE)
            for pattern in database_patterns
        )

    @staticmethod
    def _looks_unclassifiable(q: str) -> bool:
        """Reject clearly unusable one-token messages."""

        tokens = q.split()

        if len(tokens) == 1:
            token = tokens[0]

            # Examples:
            # "???"
            # "12345"
            # "!!!"
            if not re.search(r"[A-Za-z]{2,}", token):
                return True

        return False


# ---------------------------------------------------------------------
# Ambiguity handling
# ---------------------------------------------------------------------

_VAGUE_REFERENT_RE = re.compile(
    r"\b("
    r"it|that|this|them|those|"
    r"their|theirs|his|her|hers|its|"
    r"the order|"
    r"the one|"
    r"the record"
    r")\b",
    re.IGNORECASE,
)

# A concrete proper noun or ISO date can provide a referent.
_ENTITY_HINT_RE = re.compile(
    r"\b[A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*\b"
    r"|\b\d{4}-\d{2}-\d{2}\b"
)


def is_ambiguous(
    question: str,
    has_conversation_context: bool,
) -> bool:
    """Return True when a database question relies on an unresolved
    vague reference.

    Examples:
        "Show it"
        "What about that record?"

    If conversation context exists, the pipeline can potentially resolve
    the reference from previous turns.
    """

    if has_conversation_context:
        return False

    q = (question or "").strip()

    if not q:
        return False

    # Long questions usually contain enough context to retrieve against.
    if len(q.split()) > 8:
        return False

    # No vague reference -> not ambiguous.
    if not _VAGUE_REFERENT_RE.search(q):
        return False

    # A concrete entity/date may resolve the reference.
    if _ENTITY_HINT_RE.search(q):
        return False

    return True