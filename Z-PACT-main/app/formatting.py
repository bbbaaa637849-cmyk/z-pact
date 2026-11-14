import re

# Separator pattern: matches one or more spaces, dots, slashes, backslashes, colons, or hyphens
SEP = r'[\.\/\\:\-\s]+'

# Invisible directional / formatting Unicode chars pasted from chats (WhatsApp / Telegram)
_INVISIBLE_CHARS_RE = re.compile(r'[\u200b-\u200f\u202a-\u202e\u061c\ufeff]+')
_AR_DIACRITICS_TATWEEL_RE = re.compile(r'[\u064b-\u065f\u0670\u0640]')

# Professional / personal / honorific titles that should be preserved as-is WITHOUT adding "أ /"
PROFESSIONAL_TITLES_REGEX = re.compile(
    r'^(?:'
    r'[أاإ]\.?\s*د|[أاإ]\s*[/\\]\s*د|د\.?\s*م|'
    r'دكتور|الدكتور|دكتورة|الدكتورة|دكتوره|الدكتوره|د|'
    r'مهندس|المهندس|مهندسة|المهندسة|مهندسه|المهندسه|بشمهندس|البشمهندس|باشمهندس|الباشمهندس|بشمهندسة|باشمهندسة|م|'
    r'مستر|المستر|مس|المس|ميس|الميس|مسز|تيتشر|سير|'
    r'أستاذة|الأستاذة|استاذة|الاستاذة|أستاذه|الأستاذه|استاذه|الاستاذه|'
    r'أبلة|ابلة|الأبلة|الابلة|أبله|ابله|آنسة|انسة|الآنسة|الانسة|مدام|'
    r'كابتن|الكابتن|كوتش|الكوتش|شيخ|الشيخ|معلم|المعلم|معلمة|المعلمة|محاضر|المحاضر|مدرب|المدرب|'
    r'بروفيسور|البروفيسور|مستشار|المستشار|مستشارة|المستشارة|لواء|اللواء|'
    r'dr|eng|prof|mr|mrs|ms|miss|mister|mst|teacher|coach|capt|captain|sir|doctor|engineer|sheikh|madam|madame|master'
    r')' + SEP,
    re.UNICODE | re.IGNORECASE
)

# Regex for detecting standard masculine "أ /" aliases to normalize cleanly without doubling
TEACHER_PREFIX_REGEX = re.compile(
    r'^(?:أستاذ|الأستاذ|استاذ|الاستاذ|إستاذ|الإستاذ|أ|ا|إ)' + SEP,
    re.UNICODE | re.IGNORECASE
)

# Regex for detecting phone/contact prefixes
PHONE_PREFIX_REGEX = re.compile(
    r'^(?:تليفون|هاتف|موبايل|جوال|ت)' + SEP,
    re.UNICODE | re.IGNORECASE
)


def _match_stripped_prefix_end(text: str, pattern: re.Pattern) -> int:
    """Match pattern after ignoring Arabic diacritics/tatweel and map match length back to text."""
    index_map: list[int] = []
    norm_chars: list[str] = []
    for idx, ch in enumerate(text):
        if _AR_DIACRITICS_TATWEEL_RE.match(ch):
            continue
        norm_chars.append(ch)
        index_map.append(idx)
    if not norm_chars:
        return 0
    m = pattern.match("".join(norm_chars))
    if not m or m.end() == 0:
        return 0
    last_norm_idx = m.end() - 1
    return index_map[last_norm_idx] + 1


def format_instructor_name(name: str) -> str:
    """
    Intelligently formats an instructor's name:
    1. Strips invisible Unicode directional marks and messy repeated "أ /" prefixes.
    2. Preserves titles such as "مستر", "مس", "أستاذة", "دكتور", "مهندس", "م /", "د /", etc.
       without prepending "أ / ".
    3. Standardizes plain names or "أستاذ / أ. / ا/" variations to "أ / <name>".
    """
    if not name or not isinstance(name, str):
        return ""

    cleaned = _INVISIBLE_CHARS_RE.sub("", name)
    cleaned = " ".join(cleaned.strip().split())
    if not cleaned:
        return ""

    # Check if input already starts with a preserved title (e.g. مستر, مس, أستاذة, دكتور, مهندس)
    if _match_stripped_prefix_end(cleaned, PROFESSIONAL_TITLES_REGEX):
        return cleaned

    # Strip all occurrences of standard teacher prefixes (handles "أ / أ /", "ا/", "أستاذ", "الاستاذ")
    while True:
        cut_end = _match_stripped_prefix_end(cleaned, TEACHER_PREFIX_REGEX)
        if cut_end > 0:
            cleaned = cleaned[cut_end:].strip()
        else:
            break

    # After stripping "أ /" or "أستاذ", check again if what remains is a preserved title
    # (e.g. "أ / مستر أحمد" -> "مستر أحمد", "أ / دكتور محمد" -> "دكتور محمد")
    if _match_stripped_prefix_end(cleaned, PROFESSIONAL_TITLES_REGEX):
        return cleaned

    return f"أ / {cleaned}" if cleaned else ""


def clean_phone_number(phone: str) -> str:
    """
    Strips any contact/phone prefixes ("ت.", "ت /", "ت\\", "تليفون:", "هاتف:", etc.)
    and returns just the pure phone number string without any prefix.
    Used especially for the cover page (Page 1) to display the phone number only.
    """
    if not phone or not isinstance(phone, str):
        return ""
        
    cleaned = phone.strip()
    
    # Strip all occurrences of phone prefixes (handles double prefixes like "ت / ت /")
    while True:
        m = PHONE_PREFIX_REGEX.match(cleaned)
        if m:
            cleaned = cleaned[m.end():].strip()
        else:
            break
            
    # Clean any residual slashes, colons, hyphens at the start
    cleaned = cleaned.lstrip('/\\:.- ')
    return cleaned


def format_contact_phone(phone: str) -> str:
    """
    Intelligently formats a phone/contact string:
    1. Strips any messy or repeated contact prefixes ("ت.", "ت /", "ت\\", "تليفون:").
    2. Standardizes by prepending "ت / ".
    """
    cleaned = clean_phone_number(phone)
    return f"ت / {cleaned}" if cleaned else ""
