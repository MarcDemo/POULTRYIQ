EGGS_PER_TRAY = 30
EGG_WEIGHT_CAPTURE_ENABLED = False


def format_eggs_as_trays(total_eggs):
    """Return an egg count as full trays plus loose eggs."""
    try:
        egg_count = max(int(total_eggs or 0), 0)
    except (TypeError, ValueError):
        return "0 eggs"

    trays, loose_eggs = divmod(egg_count, EGGS_PER_TRAY)
    parts = []
    if trays:
        parts.append(f"{trays} {'tray' if trays == 1 else 'trays'}")
    if loose_eggs:
        parts.append(f"{loose_eggs} {'egg' if loose_eggs == 1 else 'eggs'}")
    return " and ".join(parts) if parts else "0 eggs"
