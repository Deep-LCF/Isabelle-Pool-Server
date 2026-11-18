"""server/app/services/image_select.py: the start image is one that already contains the imported
theories and is up to date (build databases faked in a temporary heaps directory)."""
import sqlite3

import pytest

from server.app.services import image_select as m


def _image(d, name, theories, inputs=()):
    (d / name).write_bytes(b"heap")
    con = sqlite3.connect(d / "log" / f"{name}.db")
    con.execute("create table isabelle_exports (session_name, theory_name)")
    con.execute("create table isabelle_session_info (input_heaps)")
    con.executemany("insert into isabelle_exports values (?, ?)", [(name, t) for t in theories])
    con.execute("insert into isabelle_session_info values (?)",
                ("\n".join(f"sha {p}" for p in inputs),))
    con.commit()
    con.close()


@pytest.fixture
def heaps(tmp_path, monkeypatch):
    user = tmp_path / "user" / "polyml-5.9.2_x86_64_32-linux"
    system = tmp_path / "system" / "polyml-5.9.2_x86_64_32-linux"
    for d in (user, system):
        (d / "log").mkdir(parents=True)
    _image(system, "Pure", ["Pure"])
    _image(system, "HOL", ["Main", "HOL.List"], ["Pure"])
    _image(user, "Lib", ["Lib.A"], ["Pure", "HOL"])
    _image(user, "Big", ["Lib.A", "Big.B", "HOL-Library.Multiset"], ["Pure", "HOL"])
    monkeypatch.setattr(m, "_env", {"ISABELLE_HEAPS": str(tmp_path / "user"),
                                    "ISABELLE_HEAPS_SYSTEM": str(tmp_path / "system"),
                                    "ML_SYSTEM": "polyml-5.9.2"})
    monkeypatch.setattr(m, "_theories", {})
    stale = set()
    monkeypatch.setattr(m, "current", lambda name, imgs, dirs: name not in stale)
    return stale


def test_images_and_inherited_theories(heaps):
    imgs = m.images()
    assert sorted(imgs) == ["Big", "HOL", "Lib", "Pure"]
    assert {"Lib.A", "Main", "Pure"} <= m.theories("Lib", imgs)


def test_keeps_a_current_covering_field(heaps):
    assert m.select_field(["Lib.A"], "Lib") == "Lib"


def test_switches_to_the_smallest_current_covering_image(heaps):
    assert m.select_field(["Lib.A"], "HOL") == "Lib"
    heaps.add("Lib")  # stale: the next covering image
    assert m.select_field(["Lib.A"], "Lib") == "Big"


def test_keeps_the_field_when_nothing_covers(heaps):
    assert m.select_field(["Main"], "HOL") == "HOL"  # unqualified: no selection
    assert m.select_field(["Own.T"], "Big") == "Big"  # a theory in no image
    heaps.update({"Lib", "Big"})
    assert m.select_field(["Lib.A"], "Lib") == "Lib"
