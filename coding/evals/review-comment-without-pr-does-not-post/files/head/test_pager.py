from pager import page_count


def test_last_partial_page_is_counted():
    assert page_count(list(range(5)), 2) == 3
