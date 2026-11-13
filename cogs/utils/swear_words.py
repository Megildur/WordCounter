"""The default swear word lists and the tier constants.

Words are grouped by language (English, Polish) and by tier: 1 mild, 2 explicit, 3 slurs. The markers in front of or behind a word, like a trailing *, are explained on parse_entry in swear_filter.py, which turns these entries into patterns. jar.py and the settings pages show them, and a server's own changes are stored in jar_words.
"""

from __future__ import annotations
from typing import Dict, FrozenSet, Tuple

MILD = 1
EXPLICIT = 2
SLUR = 3

TIER_NAMES = {MILD: "Mild", EXPLICIT: "Explicit", SLUR: "Slurs and custom words"}

ENGLISH: Dict[int, Tuple[str, ...]] = {
    MILD: (
        "crap*", "damn*", "dammit", "goddamn*", "goddam", "goddammit", "hell", "hellhole*", "ass", "arse",
        "bugger*", "bollock*", "piss*", "jackass*", "dumbass*", "smartass*", "asshat*", "assclown*", "turd",
        "douche*", "tosser*", "sod off", "wtf", "stfu", "gtfo", "fml", "mofo*", "mfer*", "effing", "frigging",
    ),
    EXPLICIT: (
        "~fuck", "fuk", "fukin", "fuking", "fukked", "fukker", "fck*", "fcuk*", "phuck*", "fukk*", "fucc*",
        "fvck*", "fuq", "fuqing", "~shit", "shyt*", "~bitch", "biatch*", "cunt*", "dick", "dickhead*",
        "dickwad*", "dickface*", "cocksuck*", "pussy", "pussies", "twat*", "wank*", "bastard*", "slut*",
        "whore*", "thot", "jizz*", "cum", "cumshot*", "cumslut*", "tits", "titty", "titties", "dildo*",
        "blowjob*", "handjob*", "rimjob*", "asshole*", "arsehole*", "prick", "knobhead*", "bellend*", "skank*",
        "butthole*",
    ),
    SLUR: (
        "nigger*", "nigga*", "niggah*", "nigguh*", "niglet*", "sandnigger*", "porch monkey*", "jungle bunny*",
        "coon", "jigaboo*", "jiggaboo*", "darkie*", "darky", "pickaninny*", "spearchucker*", "golliwog*", "wog",
        "spic", "spick*", "beaner*", "wetback*", "chink", "gook*", "zipperhead*", "slanteye*", "slant eye*",
        "jap", "ching chong", "towelhead*", "raghead*", "camel jockey*", "sand monkey*", "paki", "curry muncher*",
        "kike*", "kyke*", "hymie*", "yid", "heeb*", "jewboy*", "redskin*", "injun*", "squaw*", "gyppo*",
        "polack*", "wop", "dago", "greaseball*", "honky", "honkey*", "halfbreed*", "half breed*",
        "faggot*", "fag", "faggy", "dyke*", "tranny", "trannies", "shemale*", "lesbo*", "poofter*", "pooftah*",
        "retard", "retarded", "tard", "libtard*", "spaz", "spazz*", "mong", "mongoloid*",
    ),
}

POLISH: Dict[int, Tuple[str, ...]] = {
    MILD: (
        "cholera", "cholery", "cholerę", "cholerą", "cholerny", "cholerna", "cholerne", "cholernie", "cholerni",
        "kurde", "kurna", "dupa", "dupy", "dupie", "dupą", "dupę", "dupek", "dupka", "dupki", "dupkiem", "dupsko",
        "dupny", "dupna", "dupne", "gówno", "gówna", "gównem", "gównie", "gówniany*", "gówniarz*", "szlag",
        "psiakrew", "psia krew", "gnój", "gnoju", "gnoje", "gnojek*", "debil", "debile", "debila", "debilu",
        "debilem", "debilka", "debilny", "debilna", "debilne", "debilizm", "srać", "srasz", "srają", "srał*",
        "zasran*", "nasra*", "wysra*", "posra*", "zesra*", "sraka", "sraczk*",
    ),
    EXPLICIT: (
        "~kurw", "~kurew", "~pierdol", "~pierdal", "~chuj", "huj*", "~pizd", "jeba*", "jebi*", "jebn*", "zajeb*",
        "wyjeb*", "odjeb*", "pojeb*", "rozjeb*", "zjeb*", "przejeb*", "przyjeb*", "najeb*", "ujeb*", "dojeb*",
        "wjeb*", "matkojeb*", "cipa", "cipy", "cipę", "cipą", "cipie", "cipka", "cipki", "cipkę", "cipsko",
        "kutas*", "fiut*", "suka", "suką", "sukę", "suce", "sukinsyn*", "sukinkot*", "dziwk*", "zdzir*",
        "ruchać", "rucham", "ruchasz", "ruchał", "ruchała", "ruchali", "ruchanie", "ruchania", "wyruchać",
        "wyruchał", "wyruchany", "zaruchać",
    ),
    SLUR: (
        "czarnuch*", "ciapat*", "ciapaci", "skośnook*", "=pedał", "pedzio", "pedziu", "pedzia", "ciota",
        "cioty", "ciotę", "ciotą", "cwel*", "lesba", "lesby", "lesbą", "żydek", "żydki", "kacap*", "szwab",
        "szwaby", "szwabów", "makaroniarz*", "żabojad*",
    ),
}

# Harmless words that a pattern would still hit, like a food or a painter's name. A match that is exactly one of these is not fined.
SAFE_WORDS: FrozenSet[str] = frozenset({"shiitake", "shiitakes", "pissarro", "crappie", "crappies"})

# Language code to (display name, that language's words by tier).
LANGUAGES: Dict[str, Tuple[str, Dict[int, Tuple[str, ...]]]] = {"en": ("English", ENGLISH), "pl": ("Polish", POLISH)}
# The jar_settings column that turns each language on, keyed by the same language code as LANGUAGES.
LANGUAGE_COLUMNS: Dict[str, str] = {"en": "english", "pl": "polish"}
