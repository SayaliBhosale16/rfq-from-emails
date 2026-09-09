from rfq import normalize


def test_fold_text_unicode_fraction_and_symbol():
    assert normalize.fold_text("½") == "1/2"
    assert normalize.fold_text("20 × ½ 90 elbows") == "20 x 1/2 90 elbows"


def test_fold_text_dash_and_prime():
    assert normalize.fold_text("50 of the 1/2″ black iron 90s") == (
        "50 of the 1/2\" black iron 90s"
    )
    assert normalize.fold_text("6 – 3/4 brass") == "6 - 3/4 brass"


def test_fold_size_equivalence():
    assert normalize.fold_size(".5") == normalize.fold_size("0.500") == normalize.fold_size("1/2")
    assert normalize.fold_size("1/2") == "1/2"
    assert normalize.fold_size(None) is None


def test_fold_size_unseen_decimal_falls_back_to_float_equality():
    # 0.375 isn't a literal key anywhere, but must fold to the same canonical
    # form as the catalog's "3/8" via decimal-equality fallback.
    assert normalize.fold_size("0.375") == normalize.fold_size("3/8") == "3/8"


def test_fold_material_known():
    assert normalize.fold_material("Brass") == "BR"
    assert normalize.fold_material("18-8 stainless") == "SS"
    assert normalize.fold_material("black iron") == "BI"
    assert normalize.fold_material("blk iron") == "BI"


def test_fold_material_pvc_is_named_but_unknown_not_none():
    # No PVC material exists anywhere in catalog.csv -- this sentinel is the
    # exact mechanism behind not_in_catalog for E014-19 / E011-2.
    assert normalize.fold_material("pvc") == normalize.MATERIAL_NAMED_BUT_UNKNOWN


def test_fold_material_none_when_nothing_named():
    assert normalize.fold_material("some fittings") is None
    assert normalize.fold_material(None) is None


def test_fold_unit_word():
    assert normalize.fold_unit_word("reels") == "RL"
    assert normalize.fold_unit_word("2 boxes") == "BX"
    assert normalize.fold_unit_word("pairs") == "PR"
    assert normalize.fold_unit_word("meters") == "M"


def test_fold_unit_word_vague_quantifier():
    assert normalize.fold_unit_word("a couple of") == normalize.VAGUE_UNIT
    assert normalize.fold_unit_word("a couple") == normalize.VAGUE_UNIT
    assert normalize.fold_unit_word("several") == normalize.VAGUE_UNIT


def test_fold_unit_word_none_when_no_unit():
    assert normalize.fold_unit_word("brass elbows") is None


def test_fold_family():
    assert normalize.fold_family("20 x 1/2 90 elbows") == "EL90"
    assert normalize.fold_family("brass ball valves") == "BV"
    assert normalize.fold_family("hex cap screw 1/2-13") == "HHCS"


def test_catalog_and_query_folding_cannot_drift_apart():
    """The contract: folding a real catalog row's size/material and folding a
    query string built to describe that same row must produce equal keys.
    """
    # HHCS-1/4-20x3/4-SS: size=1/4, material=SS (from data/catalog.csv)
    catalog_size = normalize.fold_size("1/4")
    catalog_material = normalize.fold_material("SS")

    query_size = normalize.fold_size(".25")
    query_material = normalize.fold_material("18-8 stainless")

    assert catalog_size == query_size == "1/4"
    assert catalog_material == query_material == "SS"
