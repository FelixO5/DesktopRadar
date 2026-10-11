from radarscope.aircraft_types import EMITTER_CATEGORY, TYPE_CATEGORY, Category, _TYPES, classify
from radarscope.airlines import AIRLINES, airline_code, operator_name


def test_airline_from_callsign():
    assert operator_name("BAW123") == "British Airways"
    assert operator_name("baw12k ") == "British Airways"
    assert operator_name("AAL100") == "American Airlines"
    assert operator_name("RRR4401") == "Royal Air Force"
    assert operator_name("EZY45AB") == "easyJet"


def test_non_airline_callsigns():
    assert airline_code("GABCD") is None      # registration used as callsign
    assert airline_code("N123AB") is None
    assert operator_name("ZZZ123") is None    # unknown designator
    assert operator_name("") is None


def test_military_fallbacks_by_address_block():
    assert operator_name("TARTN51", "43c123", military=True) == "UK Military"
    assert operator_name("DUKE11", "ae1234", military=True) == "US Military"
    assert operator_name("VIPER1", "3f0000", military=True) == "Military"
    assert operator_name("RRR123", "43c123", military=True) == "Royal Air Force"  # callsign wins


def test_designators_are_three_capital_letters():
    assert all(len(k) == 3 and k.isalpha() and k.isupper() for k in AIRLINES)


def test_classify_by_type_then_emitter_category():
    assert classify("A320") is Category.NARROWBODY
    assert classify("b77w") is Category.WIDEBODY
    assert classify("A388") is Category.HEAVY
    assert classify("EUFI") is Category.FIGHTER
    assert classify("C172") is Category.LIGHT
    assert classify("EC35") is Category.HELICOPTER
    assert classify("C17") is Category.MIL_TRANSPORT
    assert classify("CL60") is Category.BIZJET
    assert classify("DH8D") is Category.TURBOPROP
    assert classify("", "B1") is Category.GLIDER
    assert classify("XXXX", "A7") is Category.HELICOPTER
    assert classify("", "") is Category.UNKNOWN


def test_no_type_code_in_two_categories():
    seen = {}
    for cat, codes in _TYPES.items():
        for code in codes.split():
            assert code not in seen, f"{code} in {seen.get(code)} and {cat}"
            seen[code] = cat
    assert len(TYPE_CATEGORY) == len(seen)
    assert set(EMITTER_CATEGORY.values()) <= set(Category)
