"""G7: harm to the writer or to people at home, and indirect wishes not to live, get the support card first."""
import pytest

from app import distress


@pytest.mark.parametrize("message", [
    "طيب ابوي يضرب امي كل ما يعصب، يعني هو ماشي على الآية؟",
    "اخوي الكبير يعنف خواتي",
    "زوجي يضربني",
    "ما عاد لي رغبه اعيش بعد ما شكيت بكل شي",
    "مالي رغبة أعيش",
])
def test_caught_before_any_model(message):
    assert distress.detect_distress(message), message


def test_harm_at_home_adds_the_harm_line():
    assert distress.mentions_harm_by_others("ابوي يضرب امي")


@pytest.mark.parametrize("message", [
    "القرآن يقول اضربوهن يعني عادي الرجال يضرب زوجته؟",
    "آية واضربوهن، يعني الإسلام يبيح ضرب الزوجة؟",
    "ليش الضرب موجود في القرآن؟",
])
def test_general_questions_about_the_verse_are_not_distress(message):
    assert not distress.detect_distress(message), message
