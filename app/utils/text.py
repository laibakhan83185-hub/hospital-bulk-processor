def normalize(value: str) -> str:
    """Lowercase and collapse whitespace, for matching names and addresses."""
    return " ".join(value.casefold().split())
