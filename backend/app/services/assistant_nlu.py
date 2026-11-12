"""Lightweight language and payment-intent hints for safe assistant routing.

General language understanding remains the configured Ollama model's job.
These rules only protect database-backed payment facts and make the offline
fallback useful for the most common short prompts.
"""

import re
import unicodedata


SCRIPT_PATTERNS = (
    ("kn", "native", re.compile(r"[\u0c80-\u0cff]")),
    ("hi", "native", re.compile(r"[\u0900-\u097f]")),
    ("ta", "native", re.compile(r"[\u0b80-\u0bff]")),
    ("te", "native", re.compile(r"[\u0c00-\u0c7f]")),
    ("ml", "native", re.compile(r"[\u0d00-\u0d7f]")),
)

ROMANIZED_HINTS = {
    "kn": {"ninna", "hesaru", "enu", "nanage", "madu", "maadi", "nanna", "eshtu", "ide", "idara", "artha", "beku", "nimma"},
    "hi": {"namaste", "mera", "meri", "naam", "kya", "hai", "kitna", "kitni", "mujhe", "madad", "batao", "dikhao", "mere"},
    "ta": {"vanakkam", "enakku", "ungal", "unnoda", "peyar", "enna", "evvalavu", "eththanai", "irukku", "sollu", "pannu"},
    "te": {"namaskaram", "naaku", "naa", "mee", "peru", "enti", "enni", "entha", "unnayi", "cheppu", "undi"},
    "ml": {"namaskaram", "ente", "enikku", "ningalude", "peru", "enthanu", "ethra", "undo", "parayu", "sahayikku"},
}

LANGUAGE_NAMES = {
    "en": "English",
    "kn": "Kannada",
    "hi": "Hindi",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
}

NEUTRAL_FOLLOWUPS = {
    "and?",
    "and",
    "and why?",
    "and why",
    "and what about that?",
    "and what about that",
    "why?",
    "why",
    "how many?",
    "how many",
    "what about that?",
    "what about that",
    "what about them?",
    "what about them",
    "tell me more",
    "more",
    "continue",
    "ಇನ್ನೂ",
    "और?",
    "ஏன்?",
    "ఎందుకు?",
    "എന്തുകൊണ്ട്?",
}


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def detect_language(text: str) -> tuple[str, str]:
    """Return (language code, input script style: native/latin/mixed)."""
    folded = _fold(text)
    if any(word in folded for word in ("reply in english", "in english please", "answer in english", "speak english")):
        return "en", "latin"
    explicit_languages = (
        ("kn", ("in kannada", "reply in kannada", "answer in kannada", "kannada please")),
        ("hi", ("in hindi", "reply in hindi", "answer in hindi", "hindi please")),
        ("ta", ("in tamil", "reply in tamil", "answer in tamil", "tamil please")),
        ("te", ("in telugu", "reply in telugu", "answer in telugu", "telugu please")),
        ("ml", ("in malayalam", "reply in malayalam", "answer in malayalam", "malayalam please")),
    )
    for code, phrases in explicit_languages:
        if any(phrase in folded for phrase in phrases):
            transliteration = "transliteration" in folded or "english letters" in folded or "romanized" in folded
            return code, "latin" if transliteration else "native"
    for code, style, pattern in SCRIPT_PATTERNS:
        if pattern.search(text):
            if re.search(r"[A-Za-z]", text):
                return code, "mixed"
            return code, style

    tokens = set(re.findall(r"[a-z]+", folded))
    scores = {code: len(tokens & hints) for code, hints in ROMANIZED_HINTS.items()}
    code, score = max(scores.items(), key=lambda item: item[1])
    if score:
        return code, "latin"
    if any(character.isalpha() and ord(character) > 127 for character in text):
        return "auto", "native"
    return "en", "latin"


def resolve_language(text: str, history: list[dict] | None = None) -> tuple[str, str]:
    """Detect a language change, otherwise preserve language for terse turns."""
    folded = _fold(text)
    if folded in {_fold(value) for value in NEUTRAL_FOLLOWUPS}:
        for item in reversed(history or []):
            if item.get("role") == "user" and item.get("content"):
                return detect_language(str(item["content"]))
    return detect_language(text)


def _canonical_payment_text(text: str) -> str:
    folded = _fold(text)
    folded = re.sub(r"\bwhat['’]s\b", "what is", folded)
    folded = re.sub(r"\bhow['’]s\b", "how is", folded)
    replacements = {
        "wat": "what",
        "wats": "what is",
        "whats": "what is",
        "abt": "about",
        "hapening": "happening",
        "hapenning": "happening",
        "happening": "happening",
        "transactons": "transactions",
        "transations": "transactions",
        "tranactions": "transactions",
        "paymnt": "payment",
        "pymt": "payment",
    }
    return " ".join(replacements.get(token, token) for token in re.findall(r"[\w]+|[^\w\s]", folded))


def detect_payment_intent(text: str, history: list[dict] | None = None) -> tuple[str | None, str]:
    """Recognize only requests that need exact SQLite payment facts."""
    language, _style = resolve_language(text, history)
    folded = _canonical_payment_text(text)
    words = set(re.findall(r"[a-z]+", folded))

    if re.search(r"\btxn_[a-zA-Z0-9_-]+\b", text):
        return "transaction", language

    transliterated_count = (
        language == "kn" and bool(words & {"eshtu", "esthu"}) and bool(words & {"transaction", "transactions", "ide"})
    ) or (
        language == "hi" and bool(words & {"kitna", "kitni", "kitne"}) and bool(words & {"transaction", "transactions", "hai", "hain"})
    ) or (
        language == "ta" and bool(words & {"evvalavu", "eththanai"}) and bool(words & {"transaction", "transactions", "irukku"})
    ) or (
        language == "te" and bool(words & {"enni", "entha"}) and bool(words & {"transaction", "transactions", "unnayi", "undi"})
    ) or (
        language == "ml" and "ethra" in words and bool(words & {"transaction", "transactions", "undo"})
    )
    script_count = (
        language == "kn" and bool(re.search(r"(ವಹಿವಾಟು|ಟ್ರಾನ್ಸಾಕ್ಷನ್).*(ಎಷ್ಟು|ಎಷ್ಟ)|ಎಷ್ಟು.*(ವಹಿವಾಟು|ಟ್ರಾನ್ಸಾಕ್ಷನ್)", text))
    ) or (
        language == "hi" and bool(re.search(r"(लेनदेन|ट्रांजैक्शन).*(कितने|कितनी|कितना)|कितने.*(लेनदेन|ट्रांजैक्शन)", text))
    ) or (
        language == "ta" and bool(re.search(r"(பரிவர்த்தனை|டிரான்சாக்ஷன்).*(எத்தனை|எவ்வளவு)|எத்தனை.*பரிவர்த்தனை", text))
    ) or (
        language == "te" and bool(re.search(r"(లావాదేవీ|ట్రాన్సాక్షన్).*(ఎన్ని|ఎంత)|ఎన్ని.*లావాదేవీ", text))
    ) or (
        language == "ml" and bool(re.search(r"(ഇടപാട്|ട്രാൻസാക്ഷൻ).*(എത്ര)|എത്ര.*ഇടപാട്", text))
    )

    if re.search(r"\b(failed|failure|declined|unsuccessful)\b", folded) or re.search(r"(പരാജയപ്പെട്ട|ವಿಫಲ|असफल|தோல்வியடைந்த|విఫల|പരാജയ)", text):
        return "failed", language
    if re.search(r"\b(pending|awaiting|in progress)\b", folded) or re.search(r"(ಬಾಕಿ|लंबित|நிலுவை|పెండింగ్|കാത്തിരിക്കുന്നു)", text):
        return "pending", language
    if re.search(r"\b(successful|succeeded|approved)\b", folded) or re.search(r"(ಯಶಸ್ವಿ|सफल|வெற்றிகரமான|విజయవంతమైన|വിജയകരമായ)", text):
        return "successful", language
    if any(word in folded for word in ("suspicious", "unusual", "high risk", "fraud")) or re.search(r"(ಅನುಮಾನಾಸ್ಪದ|ಸಂಶಯಾಸ್ಪದ|संदिग्ध|சந்தேகமான|అనుమానాస్పద|സംശയാസ്പദ)", text):
        return "risk", language
    if transliterated_count or script_count:
        return "count", language
    if re.search(r"\b(how many|count|number of)\b.*\b(transactions?|payments?)\b|\b(transactions?|payments?)\s+do i have\b", folded):
        return "count", language
    if any(word in folded for word in ("volume", "total amount", "total value", "transaction total")) or re.search(
        r"(ವಹಿವಾಟಿನ ಮೊತ್ತ|ಒಟ್ಟು ಮೊತ್ತ|कुल लेनदेन राशि|कुल राशि|பரிவர்த்தனை தொகை|மொத்த தொகை|లావాదేవీ మొత్తం|మొత్తం విలువ|ഇടപാട് തുക|ആകെ തുക)", text
    ):
        return "volume", language
    if any(word in folded for word in ("trend", "trends", "over time", "increasing", "decreasing")) or re.search(
        r"(ಪ್ರವೃತ್ತಿ|ಹೆಚ್ಚುತ್ತಿದೆಯೇ|ಪ್ರವೃತ್ತಿಗಳು|रुझान|बढ़ रहा है|प्रवृत्ति|போக்கு|போக்குகள்|அதிகரிக்கிறதா|ధోరణి|పెరుగుతోందా|പ്രവണത|കൂടുന്നുണ്ടോ)", text
    ):
        return "trend", language
    if any(word in folded for word in ("summarize my transactions", "summary of my transactions", "business summary", "business performance report", "payment activity")) or re.search(
        r"(ವಹಿವಾಟು.*ಸಾರಾಂಶ|ವ್ಯವಹಾರ ಸಾರಾಂಶ|ಪಾವತಿ ಚಟುವಟಿಕೆ|लेनदेन.*सारांश|व्यवसाय सारांश|भुगतान गतिविधि|பரிவர்த்தனை.*சுருக்கம்|வணிக சுருக்கம்|கட்டண செயல்பாடு|లావాదేవీ.*సారాంశం|వ్యాపార సారాంశం|చెల్లింపు కార్యకలాపం|ഇടപാട്.*സംഗ്രഹം|ബിസിനസ് സംഗ്രഹം|പേയ്മെന്റ് പ്രവർത്തനം)", text
    ) or (
        "payment" in folded and any(word in folded for word in ("happening", "what happened", "activity", "summary"))
    ) or ("summar" in folded and bool(re.search(r"\b(transactions?|payments?|activity)\b", folded))):
        return "summary", language
    if (re.search(r"\b(today|tonight)\b", folded) and any(word in folded for word in ("transaction", "payment", "activity"))) or re.search(
        r"(ಇಂದು.*(ವಹಿವಾಟು|ಪಾವತಿ)|आज.*(लेनदेन|भुगतान)|இன்று.*(பரிவர்த்தனை|கட்டணம்)|ఈరోజు.*(లావాదేవీ|చెల్లింపు)|ഇന്ന്.*(ഇടപാട്|പേയ്മെന്റ്))", text
    ):
        return "today", language
    if language == "kn" and "nanna" in words and bool(words & {"transactions", "transaction"}) and bool(words & {"eshtu", "esthu"}):
        return "count", language

    # Short follow-ups inherit the last user message's payment intent.
    if _fold(text) in {_fold(value) for value in NEUTRAL_FOLLOWUPS}:
        for item in reversed(history or []):
            if item.get("role") == "user" and item.get("content"):
                previous_intent, previous_language = detect_payment_intent(str(item["content"]), [])
                if previous_intent:
                    return previous_intent, previous_language
                break
    return None, language


def _financial_rows(context: dict, key: str) -> str:
    rows = context.get(key, [])
    if not rows:
        return "No matching simulated transactions were found."
    return "\n".join(
        f"{row['id']} · {row['status']} · {row['currency']} {row['amount']:,.2f}"
        for row in rows
    )


def financial_fallback(intent: str, context: dict, language: str, style: str = "native") -> str:
    """Create a short, transparent offline answer from exact database facts."""
    statuses = context["statuses"]
    request_text = f"{context.get('requested_text', '')} {context.get('previous_text', '')}"
    today_query = bool(re.search(r"\b(today|tonight)\b", request_text, re.IGNORECASE)) or bool(
        re.search(r"(ಇಂದು|आज|இன்று|ఈరోజు|ഇന്ന്)", request_text)
    )
    if intent == "transaction":
        match = re.search(r"\btxn_[a-zA-Z0-9_-]+\b", context.get("requested_text", ""))
        record = next((row for row in context["recent_transactions"] if match and row["id"].lower() == match.group(0).lower()), None)
        if not record:
            return _localized("not_found", language, style)
        reasons = "; ".join(record.get("risk_reasons", [])) or "No notable risk reasons were recorded."
        if language == "kn" and style == "latin":
            return f"Transaction {record['id']} simulated {record['status']} payment, {record['currency']} {record['amount']:,.2f}. Risk score {record['risk_score']}/100 ({record['risk_level']}). Reasons: {reasons}. Risk score review signal matra."
        localized_transactions = {
            "kn": "ವಹಿವಾಟು {id} ಒಂದು ಸಿಮ್ಯುಲೇಟೆಡ್ {status} ಪಾವತಿ; {currency} {amount:,.2f}. ಅಪಾಯ ಮಟ್ಟ {level} ({score}/100). ಕಾರಣಗಳು: {reasons}. ಇದು ಪರಿಶೀಲನಾ ಸೂಚನೆ ಮಾತ್ರ.",
            "hi": "लेनदेन {id} एक सिम्युलेटेड {status} भुगतान है; {currency} {amount:,.2f}. जोखिम स्तर {level} ({score}/100). कारण: {reasons}. यह समीक्षा संकेत है, धोखाधड़ी का प्रमाण नहीं.",
            "ta": "பரிவர்த்தனை {id} ஒரு உருவகப்படுத்தப்பட்ட {status} கட்டணம்; {currency} {amount:,.2f}. ஆபத்து நிலை {level} ({score}/100). காரணங்கள்: {reasons}. இது மதிப்பாய்வு குறியீடு மட்டுமே.",
            "te": "లావాదేవీ {id} సిమ్యులేటెడ్ {status} చెల్లింపు; {currency} {amount:,.2f}. ప్రమాద స్థాయి {level} ({score}/100). కారణాలు: {reasons}. ఇది సమీక్ష సూచన మాత్రమే.",
            "ml": "ഇടപാട് {id} ഒരു സിമുലേറ്റഡ് {status} പേയ്‌മെന്റാണ്; {currency} {amount:,.2f}. അപകടനില {level} ({score}/100). കാരണങ്ങൾ: {reasons}. ഇത് അവലോകന സൂചന മാത്രമാണ്.",
        }
        if language in localized_transactions:
            return localized_transactions[language].format(
                id=record["id"], status=record["status"], currency=record["currency"], amount=record["amount"],
                level=record["risk_level"], score=record["risk_score"], reasons=reasons,
            )
        return (
            f"Transaction {record['id']} is a simulated {record['status']} payment for "
            f"{record['currency']} {record['amount']:,.2f}. Its recorded risk level is {record['risk_level']} "
            f"({record['risk_score']}/100). Reasons: {reasons} Risk scores are review signals, not proof of fraud."
        )
    if intent == "count":
        return _localized("count", language, style, count=context["transaction_count"])
    if intent in {"failed", "pending", "successful"}:
        key = {"failed": "failed_transactions", "pending": "pending_transactions", "successful": "successful_transactions"}[intent]
        records = context.get("today_transactions", {}).get(intent, []) if today_query else context.get(key, [])
        total = context.get("today_statuses", {}).get(intent, 0) if today_query else statuses[intent]
        day_word = {"en": "today", "kn": "ಇಂದು", "hi": "आज", "ta": "இன்று", "te": "ఈరోజు", "ml": "ഇന്ന്"}.get(language, "today")
        if language == "en":
            label = {"failed": "failed", "pending": "pending", "successful": "successful"}[intent]
            return f"There are {total} {label} simulated payments{' ' + day_word if today_query else ''}.\n{_financial_rows({'items': records}, 'items')}"
        roman_day = {"kn": "ivattu", "hi": "aaj", "ta": "indru", "te": "ee roju", "ml": "innu"}.get(language, day_word)
        day_text = roman_day if style == "latin" else day_word
        return f"{_localized(intent, language, style, count=total)}{' · ' + day_text if today_query else ''}\n{_financial_rows({'items': records}, 'items')}"
    if intent == "risk":
        return _localized("risk", language, style, count=context["high_risk_count"]) + "\n" + _financial_rows(context, "suspicious_transactions")
    if intent == "volume":
        totals = ", ".join(f"{key} {value:,.2f}" for key, value in sorted(context["volume_by_currency"].items())) or "no recorded volume"
        return _localized("volume", language, style, totals=totals)
    if intent == "trend":
        activity = context["daily_activity"]
        older = sum(item["count"] for item in activity[:7])
        newer = sum(item["count"] for item in activity[7:])
        return _localized("trend", language, style, older=older, newer=newer)
    if intent == "today":
        statuses_today = context["today_statuses"]
        if not context["today_count"]:
            return {
                "en": "There are no simulated transactions recorded today yet.",
                "kn": "ಇಂದು ಯಾವುದೇ ಸಿಮ್ಯುಲೇಟೆಡ್ ವಹಿವಾಟುಗಳು ದಾಖಲಾಗಿಲ್ಲ.",
                "hi": "आज कोई सिम्युलेटेड लेनदेन दर्ज नहीं हुआ है.",
                "ta": "இன்று உருவகப்படுத்தப்பட்ட பரிவர்த்தனைகள் எதுவும் பதிவாகவில்லை.",
                "te": "ఈరోజు సిమ్యులేటెడ్ లావాదేవీలు నమోదు కాలేదు.",
                "ml": "ഇന്ന് സിമുലേറ്റഡ് ഇടപാടുകളൊന്നും രേഖപ്പെടുത്തിയിട്ടില്ല.",
            }.get(language, "There are no simulated transactions recorded today yet.")
        totals_today = ", ".join(f"{key} {value:,.2f}" for key, value in sorted(context["today_volume_by_currency"].items())) or "no recorded volume"
        if language == "en":
            return f"Today’s records contain {context['today_count']} simulated transactions with volume {totals_today}: {statuses_today['successful']} succeeded, {statuses_today['pending']} are pending, and {statuses_today['failed']} failed."
        if language == "kn" and style == "latin":
            return f"Ivattu {context['today_count']} simulated transactions ive; volume {totals_today}: {statuses_today['successful']} successful, {statuses_today['pending']} pending, {statuses_today['failed']} failed."
        templates = {
            "kn": "ಇಂದು {count} ಸಿಮ್ಯುಲೇಟೆಡ್ ವಹಿವಾಟುಗಳಿವೆ; ಮೊತ್ತ {volume}: {successful} ಯಶಸ್ವಿ, {pending} ಬಾಕಿ, {failed} ವಿಫಲ.",
            "hi": "आज {count} सिम्युलेटेड लेनदेन हैं; राशि {volume}: {successful} सफल, {pending} लंबित, {failed} असफल.",
            "ta": "இன்று {count} உருவகப்படுத்தப்பட்ட பரிவர்த்தனைகள் உள்ளன; தொகை {volume}: {successful} வெற்றி, {pending} நிலுவை, {failed} தோல்வி.",
            "te": "ఈరోజు {count} సిమ్యులేటెడ్ లావాదేవీలు ఉన్నాయి; విలువ {volume}: {successful} విజయవంతం, {pending} పెండింగ్, {failed} విఫలం.",
            "ml": "ഇന്ന് {count} സിമുലേറ്റഡ് ഇടപാടുകളുണ്ട്; തുക {volume}: {successful} വിജയിച്ചു, {pending} കാത്തിരിക്കുന്നു, {failed} പരാജയപ്പെട്ടു.",
        }
        return templates.get(language, templates["kn"]).format(
            count=context["today_count"], volume=totals_today,
            successful=statuses_today["successful"], pending=statuses_today["pending"], failed=statuses_today["failed"],
        )
    status = context["statuses"]
    totals = ", ".join(f"{key} {value:,.2f}" for key, value in sorted(context["volume_by_currency"].items())) or "no recorded volume"
    return _localized("summary", language, style, count=context["transaction_count"], volume=totals, successful=status["successful"], pending=status["pending"], failed=status["failed"])


def _localized(intent: str, language: str, style: str, **values) -> str:
    """Small offline phrasebook; Ollama handles full multilingual conversation."""
    if language == "kn" and style == "latin":
        templates = {
            "count": "Nimma workspace-nalli {count} simulated transactions ive.",
            "failed": "Nimma records-nalli {count} failed simulated payments ive.",
            "pending": "Nimma records-nalli {count} pending simulated payments ive.",
            "successful": "Nimma records-nalli {count} successful simulated payments ive.",
            "risk": "{count} high-risk transaction signals ive. Ivugalu fraud-ge proof alla; review signals matra.",
            "volume": "Currency prakara total simulated transaction volume: {totals}. Currency conversion maadilla.",
            "trend": "Koneya 14 dinagalalli count trend: modala 7 dinagalalli {older}, itchina 7 dinagalalli {newer}.",
            "summary": "Business summary: {count} simulated transactions; volume {volume}; {successful} successful, {pending} pending, {failed} failed.",
            "greeting": "Namaskara! Naanu PayPilot AI. Neevu Kannada transliteration-nalli kelabahudu.",
            "identity": "Naanu PayPilot AI, nimma local assistant. Kannada athava bere bhashayalli maatanaadabahudu.",
            "capabilities": "Transactions, payment status, volume, risk signals mattu general questions bagge sahaya maaduttene.",
            "ai": "AI andre Artificial Intelligence. Computers data inda patterns kalitu, questions-ge uttara kodalu idu sahaya maadutte.",
            "howareyou": "Chennagiddini, sahaya maadalu siddha! Neevu enu tilidukollabeku?",
            "meaning": "Yavudara meaning beku? Aa pada athava sentence heli.",
            "limited": "Nanna local fallback-ge seemita bhasha arivu ide. Saamanya sambhashane mattu aneka bhashagaligagi Ollama mattu configured model annu start maadi.",
            "not_found": "Aa transaction ID local records-nalli sigalilla.",
        }
    elif language == "kn":
        templates = {
            "count": "ಈ ವರ್ಕ್‌ಸ್ಪೇಸ್‌ನಲ್ಲಿ {count} ಸಿಮ್ಯುಲೇಟೆಡ್ ವಹಿವಾಟುಗಳಿವೆ.",
            "failed": "ದಾಖಲೆಗಳಲ್ಲಿ {count} ವಿಫಲ ಸಿಮ್ಯುಲೇಟೆಡ್ ಪಾವತಿಗಳಿವೆ.",
            "pending": "ದಾಖಲೆಗಳಲ್ಲಿ {count} ಬಾಕಿ ಸಿಮ್ಯುಲೇಟೆಡ್ ಪಾವತಿಗಳಿವೆ.",
            "successful": "ದಾಖಲೆಗಳಲ್ಲಿ {count} ಯಶಸ್ವಿ ಸಿಮ್ಯುಲೇಟೆಡ್ ಪಾವತಿಗಳಿವೆ.",
            "risk": "{count} ಹೆಚ್ಚಿನ ಅಪಾಯದ ಸೂಚನೆಗಳಿವೆ. ಇವು ವಂಚನೆಯ ಪುರಾವೆಯಲ್ಲ; ಪರಿಶೀಲನೆಯ ಸೂಚನೆಗಳು ಮಾತ್ರ.",
            "volume": "ಕರೆನ್ಸಿ ಪ್ರಕಾರ ಒಟ್ಟು ಸಿಮ್ಯುಲೇಟೆಡ್ ವಹಿವಾಟಿನ ಮೊತ್ತ: {totals}. ಕರೆನ್ಸಿ ಪರಿವರ್ತನೆ ಮಾಡಿಲ್ಲ.",
            "trend": "ಕಳೆದ 14 ದಿನಗಳಲ್ಲಿ ವಹಿವಾಟುಗಳ ಸಂಖ್ಯೆ: ಮೊದಲ 7 ದಿನಗಳಲ್ಲಿ {older}, ಇತ್ತೀಚಿನ 7 ದಿನಗಳಲ್ಲಿ {newer}.",
            "summary": "ವ್ಯವಹಾರ ಸಾರಾಂಶ: {count} ಸಿಮ್ಯುಲೇಟೆಡ್ ವಹಿವಾಟುಗಳು; ಮೊತ್ತ {volume}; {successful} ಯಶಸ್ವಿ, {pending} ಬಾಕಿ, {failed} ವಿಫಲ.",
            "greeting": "ನಮಸ್ಕಾರ! ನಾನು PayPilot AI. ಕನ್ನಡದಲ್ಲಾಗಲಿ ಬೇರೆ ಭಾಷೆಯಲ್ಲಾಗಲಿ ಕೇಳಬಹುದು.",
            "identity": "ನಾನು PayPilot AI, ನಿಮ್ಮ ಸ್ಥಳೀಯ ಸಹಾಯಕ. ಕನ್ನಡದಲ್ಲೂ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲೂ ಮಾತನಾಡಬಹುದು.",
            "capabilities": "ವಹಿವಾಟುಗಳು, ಪಾವತಿ ಸ್ಥಿತಿ, ಮೊತ್ತ, ಅಪಾಯ ಸೂಚನೆಗಳು ಮತ್ತು ಸಾಮಾನ್ಯ ಪ್ರಶ್ನೆಗಳಲ್ಲಿ ಸಹಾಯ ಮಾಡುತ್ತೇನೆ.",
            "ai": "AI ಎಂದರೆ ಕೃತಕ ಬುದ್ಧಿಮತ್ತೆ. ದತ್ತಾಂಶದಿಂದ ಮಾದರಿಗಳನ್ನು ಕಲಿತು ಪ್ರಶ್ನೆಗಳಿಗೆ ಉತ್ತರಿಸಲು ಕಂಪ್ಯೂಟರ್‌ಗಳಿಗೆ ಇದು ಸಹಾಯ ಮಾಡುತ್ತದೆ.",
            "howareyou": "ಚೆನ್ನಾಗಿದ್ದೇನೆ, ಸಹಾಯಕ್ಕೆ ಸಿದ್ಧ! ನಿಮಗೆ ಏನು ತಿಳಿಯಬೇಕು?",
            "meaning": "ಯಾವುದರ ಅರ್ಥ ಬೇಕು? ಆ ಪದ ಅಥವಾ ವಾಕ್ಯವನ್ನು ಹೇಳಿ.",
            "limited": "ನನ್ನ ಸ್ಥಳೀಯ fallback‌ನ ಭಾಷಾ ಸಾಮರ್ಥ್ಯ ಸೀಮಿತ. ಸಹಜ ಬಹುಭಾಷಾ ಸಂಭಾಷಣೆಗಾಗಿ Ollama ಮತ್ತು ಆಯ್ಕೆ ಮಾಡಿದ ಮಾದರಿಯನ್ನು ಪ್ರಾರಂಭಿಸಿ.",
            "not_found": "ಆ transaction ID ಸ್ಥಳೀಯ ದಾಖಲೆಗಳಲ್ಲಿ ಸಿಗಲಿಲ್ಲ.",
        }
    elif language == "hi":
        templates = {
            "count": "इस वर्कस्पेस में {count} सिम्युलेटेड लेनदेन हैं.",
            "failed": "रिकॉर्ड में {count} असफल सिम्युलेटेड भुगतान हैं.",
            "pending": "रिकॉर्ड में {count} लंबित सिम्युलेटेड भुगतान हैं.",
            "successful": "रिकॉर्ड में {count} सफल सिम्युलेटेड भुगतान हैं.",
            "risk": "{count} उच्च-जोखिम संकेत मिले। ये धोखाधड़ी का प्रमाण नहीं, केवल समीक्षा के संकेत हैं.",
            "volume": "मुद्रा के अनुसार कुल सिम्युलेटेड लेनदेन राशि: {totals}. मुद्रा रूपांतरण नहीं किया गया है.",
            "trend": "पिछले 14 दिनों में लेनदेन संख्या: पहले 7 दिनों में {older}, हाल के 7 दिनों में {newer}.",
            "summary": "व्यवसाय सारांश: {count} सिम्युलेटेड लेनदेन; राशि {volume}; {successful} सफल, {pending} लंबित, {failed} असफल.",
            "greeting": "नमस्ते! मैं PayPilot AI हूँ। आप हिंदी या किसी और भाषा में पूछ सकते हैं.",
            "identity": "मैं PayPilot AI हूँ, आपका स्थानीय सहायक। मैं हिंदी और अन्य भाषाओं में बातचीत कर सकता हूँ.",
            "capabilities": "मैं लेनदेन, भुगतान स्थिति, राशि, जोखिम संकेत और सामान्य सवालों में मदद कर सकता हूँ.",
            "ai": "AI यानी कृत्रिम बुद्धिमत्ता। यह कंप्यूटर को डेटा से पैटर्न सीखकर सवालों के जवाब देने में मदद करती है.",
            "howareyou": "मैं ठीक हूँ और मदद के लिए तैयार हूँ! आप क्या जानना चाहते हैं?",
            "meaning": "आप किस शब्द या वाक्य का अर्थ जानना चाहते हैं?",
            "limited": "मेरे स्थानीय fallback की भाषा क्षमता सीमित है। स्वाभाविक बहुभाषी बातचीत के लिए Ollama और चुना हुआ मॉडल चलाएँ.",
            "not_found": "यह transaction ID स्थानीय रिकॉर्ड में नहीं मिली.",
        }
    elif language == "ta":
        templates = {
            "count": "இந்த பணியிடத்தில் {count} உருவகப்படுத்தப்பட்ட பரிவர்த்தனைகள் உள்ளன.",
            "failed": "பதிவுகளில் {count} தோல்வியடைந்த உருவகப்படுத்தப்பட்ட கட்டணங்கள் உள்ளன.",
            "pending": "பதிவுகளில் {count} நிலுவையிலுள்ள உருவகப்படுத்தப்பட்ட கட்டணங்கள் உள்ளன.",
            "successful": "பதிவுகளில் {count} வெற்றிகரமான உருவகப்படுத்தப்பட்ட கட்டணங்கள் உள்ளன.",
            "risk": "{count} உயர் ஆபத்து குறியீடுகள் உள்ளன. இவை மோசடிக்கான ஆதாரம் அல்ல; மதிப்பாய்வு குறியீடுகள் மட்டுமே.",
            "volume": "நாணயத்தின் அடிப்படையிலான மொத்த உருவகப்படுத்தப்பட்ட பரிவர்த்தனைத் தொகை: {totals}. நாணய மாற்றம் செய்யப்படவில்லை.",
            "trend": "கடந்த 14 நாட்களில் பரிவர்த்தனைகள்: முதல் 7 நாட்களில் {older}, சமீபத்திய 7 நாட்களில் {newer}.",
            "summary": "வணிகச் சுருக்கம்: {count} பரிவர்த்தனைகள்; தொகை {volume}; {successful} வெற்றி, {pending} நிலுவை, {failed} தோல்வி.",
            "greeting": "வணக்கம்! நான் PayPilot AI. தமிழிலோ வேறு மொழியிலோ கேட்கலாம்.",
            "identity": "நான் PayPilot AI, உங்கள் உள்ளூர் உதவியாளர். தமிழிலும் பிற மொழிகளிலும் உரையாட முடியும்.",
            "capabilities": "பொதுவான கேள்விகள், விளக்கங்கள், உரையாடல் மற்றும் பணியிடத்தின் பரிவர்த்தனைத் தகவல்களில் உதவுவேன்.",
            "ai": "AI என்பது செயற்கை நுண்ணறிவு. தரவிலிருந்து வடிவங்களை கற்று மொழியைப் புரிந்துகொள்ளவும் கேள்விகளுக்கு பதிலளிக்கவும் கணினிகளுக்கு இது உதவுகிறது.",
            "howareyou": "நன்றாக இருக்கிறேன், உதவத் தயாராக உள்ளேன்! என்ன தெரிந்துகொள்ள விரும்புகிறீர்கள்?",
            "meaning": "எந்தச் சொல் அல்லது வாக்கியத்தின் பொருள் வேண்டும்?",
            "limited": "எனது உள்ளூர் fallback-இன் மொழி மற்றும் பொது அறிவுத் திறன் குறைவானது. இயல்பான பன்மொழி உரையாடலுக்கு Ollama மற்றும் தேர்ந்தெடுத்த மாதிரியை இயக்கவும்.",
            "not_found": "அந்த transaction ID உள்ளூர் பதிவுகளில் இல்லை.",
        }
    elif language == "te":
        templates = {
            "count": "ఈ వర్క్‌స్పేస్‌లో {count} సిమ్యులేటెడ్ లావాదేవీలు ఉన్నాయి.",
            "failed": "రికార్డుల్లో {count} విఫలమైన సిమ్యులేటెడ్ చెల్లింపులు ఉన్నాయి.",
            "pending": "రికార్డుల్లో {count} పెండింగ్ సిమ్యులేటెడ్ చెల్లింపులు ఉన్నాయి.",
            "successful": "రికార్డుల్లో {count} విజయవంతమైన సిమ్యులేటెడ్ చెల్లింపులు ఉన్నాయి.",
            "risk": "{count} అధిక-ప్రమాద సంకేతాలు ఉన్నాయి. ఇవి మోసానికి రుజువు కాదు; సమీక్ష సూచనలు మాత్రమే.",
            "volume": "కరెన్సీ ప్రకారం మొత్తం సిమ్యులేటెడ్ లావాదేవీ విలువ: {totals}. కరెన్సీ మార్పిడి చేయలేదు.",
            "trend": "గత 14 రోజుల్లో లావాదేవీలు: మొదటి 7 రోజుల్లో {older}, ఇటీవలి 7 రోజుల్లో {newer}.",
            "summary": "వ్యాపార సారాంశం: {count} లావాదేవీలు; విలువ {volume}; {successful} విజయవంతం, {pending} పెండింగ్, {failed} విఫలం.",
            "greeting": "నమస్కారం! నేను PayPilot AI. తెలుగులో లేదా ఇతర భాషల్లో అడగండి.",
            "identity": "నేను PayPilot AI, మీ స్థానిక సహాయకుడిని. తెలుగులోనూ ఇతర భాషల్లోనూ మాట్లాడగలను.",
            "capabilities": "సాధారణ ప్రశ్నలు, వివరణలు, సంభాషణ, ఈ వర్క్‌స్పేస్ చెల్లింపు సమాచారంలో సహాయం చేస్తాను.",
            "ai": "AI అంటే కృత్రిమ మేధస్సు. డేటాలోని నమూనాలను నేర్చుకుని భాషను అర్థం చేసుకోవడానికి, ప్రశ్నలకు సమాధానం ఇవ్వడానికి కంప్యూటర్లకు ఇది సహాయపడుతుంది.",
            "howareyou": "బాగున్నాను, సహాయం చేయడానికి సిద్ధంగా ఉన్నాను! మీరు ఏమి తెలుసుకోవాలనుకుంటున్నారు?",
            "meaning": "ఏ పదం లేదా వాక్యానికి అర్థం కావాలి?",
            "limited": "నా స్థానిక fallback‌కు భాష, సాధారణ జ్ఞాన సామర్థ్యం పరిమితం. సహజ బహుభాషా సంభాషణకు Ollama మరియు ఎంచుకున్న మోడల్‌ను ప్రారంభించండి.",
            "not_found": "ఆ transaction ID స్థానిక రికార్డుల్లో కనిపించలేదు.",
        }
    elif language == "ml":
        templates = {
            "count": "ഈ വർക്ക്‌സ്‌പെയ്‌സിൽ {count} സിമുലേറ്റഡ് ഇടപാടുകളുണ്ട്.",
            "failed": "രേഖകളിൽ {count} പരാജയപ്പെട്ട സിമുലേറ്റഡ് പേയ്‌മെന്റുകളുണ്ട്.",
            "pending": "രേഖകളിൽ {count} കാത്തിരിക്കുന്ന സിമുലേറ്റഡ് പേയ്‌മെന്റുകളുണ്ട്.",
            "successful": "രേഖകളിൽ {count} വിജയകരമായ സിമുലേറ്റഡ് പേയ്‌മെന്റുകളുണ്ട്.",
            "risk": "{count} ഉയർന്ന അപകടസാധ്യതാ സൂചനകളുണ്ട്. ഇവ തട്ടിപ്പിന്റെ തെളിവല്ല; അവലോകന സൂചനകൾ മാത്രമാണ്.",
            "volume": "കറൻസി അനുസരിച്ചുള്ള ആകെ സിമുലേറ്റഡ് ഇടപാട് തുക: {totals}. കറൻസി മാറ്റം നടത്തിയിട്ടില്ല.",
            "trend": "കഴിഞ്ഞ 14 ദിവസത്തെ ഇടപാടുകൾ: ആദ്യ 7 ദിവസങ്ങളിൽ {older}, ഏറ്റവും പുതിയ 7 ദിവസങ്ങളിൽ {newer}.",
            "summary": "ബിസിനസ് സംഗ്രഹം: {count} ഇടപാടുകൾ; തുക {volume}; {successful} വിജയിച്ചു, {pending} കാത്തിരിക്കുന്നു, {failed} പരാജയപ്പെട്ടു.",
            "greeting": "നമസ്കാരം! ഞാൻ PayPilot AI. മലയാളത്തിലോ മറ്റു ഭാഷകളിലോ ചോദിക്കാം.",
            "identity": "ഞാൻ PayPilot AI, നിങ്ങളുടെ പ്രാദേശിക സഹായി. മലയാളത്തിലും മറ്റു ഭാഷകളിലും സംസാരിക്കാം.",
            "capabilities": "പൊതുവായ ചോദ്യങ്ങൾ, വിശദീകരണങ്ങൾ, സംഭാഷണം, ഈ വർക്ക്‌സ്‌പെയ്‌സിലെ പേയ്‌മെന്റ് വിവരങ്ങൾ എന്നിവയിൽ സഹായിക്കാം.",
            "ai": "AI അഥവാ കൃത്രിമ ബുദ്ധി, ഡാറ്റയിലെ മാതൃകകൾ പഠിച്ച് ഭാഷ മനസ്സിലാക്കാനും ചോദ്യങ്ങൾക്ക് മറുപടി നൽകാനും കമ്പ്യൂട്ടറുകളെ സഹായിക്കുന്നു.",
            "howareyou": "സുഖമാണ്, സഹായിക്കാൻ തയ്യാറാണ്! എന്താണ് അറിയേണ്ടത്?",
            "meaning": "ഏത് വാക്കിന്റെയോ വാക്യത്തിന്റെയോ അർത്ഥമാണ് വേണ്ടത്?",
            "limited": "എന്റെ പ്രാദേശിക fallback-ന്റെ ഭാഷാ, പൊതുവിജ്ഞാന കഴിവുകൾ പരിമിതമാണ്. സ്വാഭാവിക ബഹുഭാഷാ സംഭാഷണത്തിന് Ollamaയും തിരഞ്ഞെടുത്ത മോഡലും പ്രവർത്തിപ്പിക്കുക.",
            "not_found": "ആ transaction ID പ്രാദേശിക രേഖകളിൽ കണ്ടെത്താനായില്ല.",
        }
    else:
        templates = {
            "count": "I found {count} simulated transactions in this workspace.",
            "failed": "There are {count} failed simulated payments.",
            "pending": "There are {count} pending simulated payments.",
            "successful": "There are {count} successful simulated payments.",
            "risk": "I found {count} high-risk signals. These are review cues, not proof of fraud.",
            "volume": "Total simulated transaction volume by currency: {totals}. No currency conversion is applied.",
            "trend": "In the last 14 days, transaction counts were {older} in the first 7 days and {newer} in the latest 7 days.",
            "summary": "Business summary: {count} simulated transactions, volume {volume}, {successful} successful, {pending} pending, and {failed} failed.",
            "greeting": "Hi! I’m PayPilot AI. I can chat in multiple languages and help with general questions or payment intelligence.",
            "identity": "I’m PayPilot AI, a friendly local assistant for general conversation and simulated payment intelligence.",
            "capabilities": "I can discuss general topics, explain ideas, continue conversations, and answer payment questions from this workspace’s records.",
            "ai": "AI means artificial intelligence: computer systems that learn patterns and use them to perform tasks such as understanding language or answering questions.",
            "howareyou": "I’m doing well and ready to help. What would you like to talk about?",
            "meaning": "Which word or phrase would you like me to explain?",
            "limited": "My local fallback has limited language and general-knowledge ability. Start Ollama with the configured model for natural multilingual conversation.",
            "not_found": "I couldn’t find that transaction ID in the local records.",
        }
    return templates.get(intent, templates["limited"]).format(**values)


def general_fallback(message: str, language: str, style: str, context: dict, history: list[dict] | None = None) -> str:
    folded = _canonical_payment_text(message)
    compact = re.sub(r"[^\w\s]", "", folded).strip()
    script_greetings = ("ನಮಸ್ಕಾರ", "ನಮಸ್ತೆ", "नमस्ते", "नमस्कार", "வணக்கம்", "నమస్కారం", "నమస్కారము", "നമസ്കാരം")
    if any(phrase in message for phrase in script_greetings):
        return _localized("greeting", language, style)
    if compact in {"how are you", "how r u", "hru", "how are u", "hii how r u", "hi how r u", "hey how r u"}:
        return _localized("howareyou", language, style)
    if compact in {"hi", "hii", "hello", "hey", "hey there", "hello there", "namaste", "ನಮಸ್ಕಾರ", "ನಮಸ್ತೆ", "नमस्ते", "வணக்கம்", "నమస్కారం", "നമസ്കാരം"}:
        return _localized("greeting", language, style)
    creator_question = any(term in folded for term in ("who created you", "who made you", "who developed you", "who built you", "who created paypilot", "who made paypilot", "creator of paypilot", "founder of paypilot"))
    creator_question = creator_question or bool(re.search(r"who.*(?:founder|creator|ceo).*paypilot|(?:founder|creator|ceo).*paypilot", folded))
    if creator_question:
        return "PayPilot AI was created and developed by H B Veeresha. He is the project creator; no company executive title is claimed."
    if any(term in folded for term in ("what is your name", "who are you", "your name", "ninna hesaru enu", "ನಿನ್ನ ಹೆಸರು ಏನು", "ನಿಮ್ಮ ಹೆಸರು ಏನು", "आपका नाम क्या", "तुम्हारा नाम क्या")):
        return _localized("identity", language, style)
    if any(term in folded for term in ("what can you do", "help me", "nanage help madu", "ನನಗೆ ಸಹಾಯ ಮಾಡಿ", "ನನಗೆ ಸಹಾಯ ಮಾಡು", "मेरी मदद करो")):
        return _localized("capabilities", language, style)
    if re.search(r"\bwhat\s+is\s+(ai|artificial intelligence)\b", folded):
        return _localized("ai", language, style)
    if any(term in folded for term in ("meaning enu", "idara meaning", "ಇದರ ಅರ್ಥ", "इसका मतलब", "இதன் பொருள்", "దీని అర్థం", "ഇതിന്റെ അർത്ഥം")):
        return _localized("meaning", language, style)
    intent, _ = detect_payment_intent(message, history)
    if intent:
        context["requested_text"] = message
        return financial_fallback(intent, context, language, style)
    return _localized("limited", language, style)
