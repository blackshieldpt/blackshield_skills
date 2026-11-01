def page(items, number, size):
    """Return page `number` (1-based) of `items`, `size` items per page."""
    start = (number - 1) * size
    return items[start:start + size]


def page_count(items, size):
    """Return how many pages `items` fills at `size` items per page."""
    return len(items) // size
