from pathlib import Path

from podatlas_pipeline.geocode import GeoCity, GeoNamesIndex, slugify_city


def test_slugify_city_handles_diacritics():
    assert slugify_city("Reykjavík", "Iceland") == "reykjavik-iceland"
    assert slugify_city("Mexico City", "Mexico") == "mexico-city-mexico"


def test_geonames_lookup_disambiguates_by_country_and_population():
    cities = [
        GeoCity("paris-france", "Paris", "France", "FR", 48.8566, 2.3522, 2138551),
        GeoCity("paris-usa", "Paris", "USA", "US", 33.6609, -95.5555, 25000),
        GeoCity("kyoto-japan", "Kyoto", "Japan", "JP", 35.0116, 135.7681, 1465000),
    ]
    idx = GeoNamesIndex(cities)

    assert idx.lookup("Paris").id == "paris-france"
    assert idx.lookup("Paris", "United States").id == "paris-usa"
    assert idx.lookup("Kyoto", "Japan").id == "kyoto-japan"
    assert idx.lookup("Atlantis") is None


def test_from_geonames_file(tmp_path: Path):
    sample = "\n".join(
        [
            "1857910\tKyoto\tKyoto\tKioto,Kyoto-shi\t35.02107\t135.75385\tP\tPPLA\tJP\t\t22\t\t\t\t1467785",
            "2267057\tLisbon\tLisbon\tLisboa,Lisbonne\t38.71667\t-9.13333\tP\tPPLC\tPT\t\t14\t\t\t\t517802",
        ]
    )
    txt = tmp_path / "cities5000.txt"
    txt.write_text(sample, encoding="utf-8")

    idx = GeoNamesIndex.from_geonames_file(txt)
    assert idx.lookup("Lisboa", "Portugal").id == "lisbon-portugal"
    assert idx.lookup("Kyoto").country == "Japan"
