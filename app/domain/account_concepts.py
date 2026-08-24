"""Small, explicit accounting concept vocabulary for DART retrieval."""

from dataclasses import dataclass
from enum import StrEnum


class AccountConcept(StrEnum):
    REVENUE = "REVENUE"
    OPERATING_PROFIT = "OPERATING_PROFIT"
    NET_INCOME = "NET_INCOME"


@dataclass(frozen=True)
class AccountConceptSpec:
    aliases: tuple[str, ...]
    dart_account_names: tuple[str, ...]
    dart_account_ids: tuple[str, ...] = ()


ACCOUNT_CONCEPTS: dict[AccountConcept, AccountConceptSpec] = {
    AccountConcept.REVENUE: AccountConceptSpec(("매출", "매출액"), ("매출액",)),
    AccountConcept.OPERATING_PROFIT: AccountConceptSpec(
        ("영업이익",), ("영업이익",), ("dart_OperatingIncomeLoss",)
    ),
    AccountConcept.NET_INCOME: AccountConceptSpec(("순이익", "당기순이익"), ("당기순이익",)),
}


def resolve_account_concepts(text: str) -> tuple[AccountConcept, ...]:
    """Resolve only configured aliases; unknown accounting wording stays unresolved."""
    return tuple(
        concept
        for concept, spec in ACCOUNT_CONCEPTS.items()
        if any(alias in text for alias in spec.aliases)
    )


def accepted_dart_account_names(concepts: tuple[AccountConcept, ...]) -> tuple[str, ...]:
    names: list[str] = []
    for concept in concepts:
        for name in ACCOUNT_CONCEPTS[concept].dart_account_names:
            if name not in names:
                names.append(name)
    return tuple(names)


def dart_account_matches(concept: AccountConcept, *, account_name: str, account_id: str | None) -> bool:
    spec = ACCOUNT_CONCEPTS[concept]
    return account_name in spec.dart_account_names or account_id in spec.dart_account_ids


__all__ = [
    "ACCOUNT_CONCEPTS",
    "AccountConcept",
    "accepted_dart_account_names",
    "dart_account_matches",
    "resolve_account_concepts",
]
