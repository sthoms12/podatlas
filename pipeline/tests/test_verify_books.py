from podatlas_pipeline.verify_books import pick_best_open_library_doc


def test_pick_best_open_library_doc_matches_author_and_isbn13():
    docs = [
        {
            "title": "Kyoto: A Cultural History",
            "author_name": ["John Dougill"],
            "isbn": ["0195301382", "9780195301380"],
            "key": "/works/OL12345W",
        },
        {
            "title": "Kyoto Travel Map",
            "author_name": ["Map Co"],
            "isbn": ["1111111111"],
            "key": "/works/OL99999W",
        },
    ]
    result = pick_best_open_library_doc("Kyoto: A Cultural History", "John Dougill", docs)
    assert result is not None
    assert result.title == "Kyoto: A Cultural History"
    assert result.author == "John Dougill"
    assert result.isbn == "9780195301380"
    assert result.open_library_key == "/works/OL12345W"


def test_pick_best_open_library_doc_filters_hallucinations():
    docs = [
        {
            "title": "Completely Unrelated Gardening Guide",
            "author_name": ["Someone Else"],
            "isbn": ["9780000000000"],
        }
    ]
    result = pick_best_open_library_doc("The Phantom City of Kyoto", "Fake Author", docs)
    assert result is None
