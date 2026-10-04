"""G5: framing sentences with any claim are dropped; neutral linking sentences pass."""
import pytest

from app.guards import check_framing

SOURCES = ["الدرر السنية", "الإسلام سؤال وجواب", "موسوعة الأحاديث النبوية", "الطبري", "OpenStax"]
HADITHS = ["إني أعلم أنك حجر لا تضر ولا تنفع"]

MSG_CHANCE = "هل الكون جاء صدفة؟"
MSG_GOD = "كيف أعرف أن الله موجود؟"
MSG_BIGBANG = "هل ذكر القرآن الانفجار العظيم؟"
MSG_KAABA = "ليش المسلمين يعبدون الكعبة؟"

BAD = [
    (MSG_CHANCE, "الكون له بداية والخالق أوجده"),  # claim, words not from the message
    (MSG_GOD, "العلماء متفقون على هذا"),
    (MSG_GOD, "أثبت العلم أن الله موجود."),
    (MSG_BIGBANG, "نعم، القرآن سبق العلم في ذكر الانفجار العظيم."),
    (MSG_BIGBANG, "هذه حقيقة علمية ثابتة."),
    (MSG_KAABA, "الطواف بالكعبة حلال وليس عبادة لها."),
    (MSG_KAABA, "قال النبي ﷺ إن الحجر لا يضر ولا ينفع."),
    (MSG_KAABA, "كما رواه البخاري في صحيحه."),
    (MSG_KAABA, "إني أعلم أنك حجر لا تضر، وهذا جوابك."),  # hadith four-gram
    (MSG_GOD, "أم خلقوا من غير شيء أم هم الخالقون"),  # mushaf four-gram
    (MSG_GOD, "قال تعالى في سورة الطور ما يجيب سؤالك."),
    (MSG_GOD, "بحسب الدرر السنية هذا هو الجواب."),
    (MSG_GOD, "هذا سؤال مهم ﴿أم خلقوا﴾ وإليك الجواب."),
    (MSG_GOD, "هذا سؤال يعرض لك ٣ أدلة."),
    (MSG_GOD, "هذا سؤال مهم (انظر الجواب)."),
    (MSG_GOD, "هذا سؤال مهم، see the answer."),
    (MSG_GOD, "سؤالك فيه شك واضح."),
    (MSG_GOD, "هذا الوسواس يزول بالجواب."),
    (MSG_GOD, "سؤال."),  # too short
    (MSG_GOD, "سؤالك مهم. " * 12),  # too long
    (MSG_GOD, "سؤال مهم. جواب مفيد. خلاصة واضحة."),  # three sentences
    (MSG_GOD, "دراسة حديثة توضح الجواب."),
    (MSG_GOD, "بإجماع أهل العلم هذا هو الجواب."),
    (MSG_GOD, "يجوز لك أن تسأل هذا السؤال."),
    (MSG_GOD, "الإجابة الصحيحة القطعية هنا."),
    (MSG_GOD, "هذا سؤال الفلاسفة والملحدين."),
    (MSG_CHANCE, "التطور يفسر نشأة الكون صدفة."),  # science term not in the message
    (MSG_CHANCE, "الانفجار العظيم يجيب عن سؤالك."),
    (MSG_GOD, "اطمئن فالله موجود قطعا."),
    (MSG_GOD, "ورد في الحديث ما يجيب سؤالك."),
]

GOOD = [
    (MSG_CHANCE, "سؤالك عن الكون مهم، وهذه خلاصة الجواب."),
    (MSG_CHANCE, "هذا سؤال مهم، وإليك الإجابة المراجعة."),
    (MSG_GOD, "سؤالك مهم، وهذه الإجابة المراجعة باختصار."),
    (MSG_GOD, "نبدأ بخلاصة الجواب، ثم التفاصيل."),
    (MSG_GOD, "هذه الإجابة المراجعة عن سؤالك بوضوح."),
    (MSG_BIGBANG, "سؤالك عن القرآن والانفجار العظيم، وهذا الجواب."),
    (MSG_BIGBANG, "هذه خلاصة الإجابة المراجعة حول هذه المسألة."),
    (MSG_KAABA, "سؤالك عن الكعبة مهم، وهذه الإجابة المراجعة."),
    (MSG_KAABA, "نوضح هذه المسألة بهدوء في الإجابة التالية."),
    (MSG_KAABA, "هذا سؤال مهم، ونعرض الجواب خطوة خطوة."),
    (MSG_CHANCE, "ستجد في الإجابة التالية ما يتعلق بسؤالك."),
    (MSG_GOD, "أوضح لك الجواب بطريقة أبسط."),
    (MSG_GOD, "هذه خلاصة مختصرة، ثم الإجابة كاملة."),
    (MSG_CHANCE, "يسعدني اهتمامك، وهذه الإجابة المراجعة."),
    (MSG_KAABA, "لفهم المسألة، نبدأ بخلاصة الجواب."),
]


@pytest.mark.parametrize("message, framing", BAD)
def test_bad_framing_is_dropped(message, framing):
    assert not check_framing(framing, message, SOURCES, HADITHS), framing


@pytest.mark.parametrize("message, framing", GOOD)
def test_neutral_framing_passes(message, framing):
    assert check_framing(framing, message, SOURCES, HADITHS), framing
