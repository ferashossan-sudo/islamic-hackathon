"""The automatic checks used to compare systems must not flag our own verbatim verses and sourced hadiths."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from auto_checks import check_text  # noqa: E402

from app import quran  # noqa: E402


def test_a_verse_copied_from_the_mushaf_is_not_a_misquote():
    verse = quran.lookup("4:34")[0].text
    assert check_text(f"قال الله ﴿{verse}﴾ [النساء: 34]")["misquoted_verses"] == 0
    both = " ".join(v.text for v in quran.lookup("23:12-13"))
    assert check_text(f"﴿{both}﴾")["misquoted_verses"] == 0


def test_a_changed_verse_is_still_a_misquote():
    assert check_text("قال تعالى «والسماء بنيناها بقوة وإنا لموسعون»")["misquoted_verses"] == 1


def test_a_long_quoted_hadith_with_its_source_after_it_is_sourced():
    text = "قال النبي ﷺ «" + "كلام " * 120 + "» (المصدر: صحيح البخاري، رقم 1 · الدرجة: صحيح)"
    assert check_text(text)["unsourced_hadith"] == 0
    assert check_text("قال النبي ﷺ إن كذا وكذا.")["unsourced_hadith"] == 1


def test_a_denied_certainty_claim_is_not_counted():
    assert check_text("فلا ندعي أن العلم أثبته ولا أنه نفاه")["certainty_claims"] == 0
    assert check_text("وقد أثبت العلم ذلك بلا شك")["certainty_claims"] == 1
