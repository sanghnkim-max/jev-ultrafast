"""Test tasks with independent outcome checks. The model's DONE choice is never evidence."""

import re
from typing import Callable, NamedTuple
from urllib.parse import parse_qs, unquote, urlparse


class Task(NamedTuple):
    label: str
    url: str
    goal: str
    check: Callable  # (final_page, agent) -> {check name: bool}
    suite: str = "core"


def body_text(agent):
    return agent.browser.evaluate("document.body.innerText") or ""


def values(page):
    return {a["label"].strip(): a.get("value") for a in page["actions"]}


def path(page):
    return unquote(urlparse(page["url"]).path)


def host(page):
    return urlparse(page["url"]).hostname or ""


def on(page, domain):
    return host(page) == domain or host(page).endswith("." + domain)


def tasks(origin):
    """Named test tasks. Real-web checks read only the final URL or page state."""
    fixture = origin + "/fixture.html?scenario="
    wild = origin + "/wildlife.html"
    core = {
        "filters": (
            "Filters · fixture",
            fixture + "travel",
            "Use the destination search and filters to find Design stays in Lisbon with Free cancellation, "
            "then open Casa Flora.",
            lambda page, agent: {
                "Casa Flora open": page["url"].endswith("#casa-flora"),
                "Design, Free cancellation, Lisbon applied": "Your filters: Design · Free cancellation enabled · "
                "Destination Lisbon" in body_text(agent),
            },
        ),
        "cheapest": (
            "Cheapest stay · fixture",
            fixture + "travel",
            "Turn on the free cancellation filter, then open the cheapest stay that remains.",
            lambda page, agent: {
                "Cheapest remaining stay open": page["url"].endswith("#casa-flora"),
                "Free cancellation applied": "Free cancellation enabled" in body_text(agent),
            },
        ),
        "paraphrase": (
            "Paraphrased article · fixture",
            fixture + "research",
            "Open the reading-room article arguing that a model being sure of itself does not mean it is right.",
            lambda page, agent: {"Uncertainty article open": page["url"].endswith("#uncertainty")},
        ),
        "wikipedia": (
            "Wikipedia · real web",
            "https://en.wikipedia.org/wiki/Main_Page",
            "Open the Wikipedia article about the logician who proved the incompleteness theorems.",
            lambda page, agent: {"Kurt Gödel article open": unquote(urlparse(page["url"]).path) == "/wiki/Kurt_Gödel"},
        ),
        "flights": (
            "Google Flights · real web",
            "https://www.google.com/travel/flights?hl=en",
            "Find one-way flights from Zurich to London on October 20, 2026, for one adult in economy. "
            "Stop when matching flight options are visible. Do not select or book a flight.",
            lambda page, agent: {
                "Search results page": urlparse(page["url"]).path == "/travel/flights/search",
                "Destination London": values(page).get("Where to?") == "London",
                "Departure Oct 20": values(page).get("Departure") == "Tue, Oct 20",
                "Flights on October 20 visible": any(
                    "Select flight" in a["label"] and "October 20" in a["label"] for a in page["actions"]
                ),
            },
        ),
    }
    wildlife = {
        # Local Wildwatch fixture: controlled difficulty, exact answers.
        "wild-search": (
            "Wildwatch · open by name",
            wild,
            "Open the Wildwatch species page for the snow leopard.",
            lambda page, agent: {"Snow leopard page open": page["url"].endswith("#snow-leopard")},
        ),
        "wild-scientific": (
            "Wildwatch · scientific-name search",
            wild,
            "Search the field guide for Ambystoma mexicanum and open that species' page.",
            lambda page, agent: {
                "Axolotl page open": page["url"].endswith("#axolotl"),
                "Search applied": "Search Ambystoma" in body_text(agent),
            },
        ),
        "wild-rarest": (
            "Wildwatch · filter and compare",
            wild,
            "Show only ocean species that are Endangered or Critically endangered, "
            "then open the one with the fewest individuals left in the wild.",
            lambda page, agent: {
                "Vaquita page open": page["url"].endswith("#vaquita"),
                "Ocean + CR,EN filters applied": "Habitat Ocean · Status CR,EN" in body_text(agent),
            },
        ),
        "wild-heaviest": (
            "Wildwatch · sort and pick",
            wild,
            "Sort the field guide by weight, heaviest first, and open the heaviest species that lives in grassland.",
            lambda page, agent: {
                "Elephant page open": page["url"].endswith("#african-elephant"),
                "Weight sort applied": "Sort weight-desc" in body_text(agent),
            },
        ),
        "wild-knowledge": (
            "Wildwatch · background knowledge",
            wild,
            "Open the Wildwatch page of the species that is the national bird of the United States.",
            lambda page, agent: {"Bald eagle page open": page["url"].endswith("#bald-eagle")},
        ),
        "wild-report": (
            "Wildwatch · report a sighting",
            wild,
            "Report a sighting of 3 red foxes in Hyde Park, London on October 3, 2026.",
            lambda page, agent: {
                "Sighting submitted": page["url"].endswith("#red-fox/reported"),
                "Species and count": "Sighting recorded: 3 × Red fox" in body_text(agent),
                "Location": "at Hyde Park" in body_text(agent),
                "Date 2026-10-03": "on 2026-10-03." in body_text(agent),
            },
        ),
        # Real wildlife sites.
        "inaturalist": (
            "iNaturalist · real web",
            "https://www.inaturalist.org/",
            "Open the iNaturalist taxon page for the snow leopard.",
            lambda page, agent: {
                "iNaturalist": on(page, "inaturalist.org"),
                "Panthera uncia taxon page": bool(re.fullmatch(r"/taxa/\d+-Panthera-uncia", path(page))),
            },
        ),
        "audubon": (
            "Audubon guide · real web",
            "https://www.audubon.org/bird-guide",
            "Open the Audubon Guide to North American Birds page for the Bald Eagle.",
            lambda page, agent: {"Bald Eagle guide page": on(page, "audubon.org")
                                 and path(page).rstrip("/") == "/field-guide/bird/bald-eagle"},
        ),
        "wiki-penguin": (
            "Wikipedia · largest penguin",
            "https://en.wikipedia.org/wiki/Main_Page",
            "Open the Wikipedia article about the largest living species of penguin.",
            lambda page, agent: {"Emperor penguin article": path(page) == "/wiki/Emperor_penguin"},
        ),
        "wiki-panda": (
            "Wikipedia · by scientific name",
            "https://en.wikipedia.org/wiki/Main_Page",
            "Open the Wikipedia article for the animal whose scientific name is Ailuropoda melanoleuca.",
            lambda page, agent: {"Giant panda article": path(page) == "/wiki/Giant_panda"},
        ),
    }
    reallife = {
        # WebVoyager-style live tasks, adapted so the final page proves the outcome. WWF and Allrecipes
        # were dropped: headless Chrome gets a bot challenge or an empty page before any agent action.
        "cambridge": (
            "Cambridge Dictionary · real web",
            "https://dictionary.cambridge.org/",
            "Look up the word 'resilience' in the Cambridge English Dictionary.",
            lambda page, agent: {"Entry for resilience": on(page, "dictionary.cambridge.org")
                                 and path(page).rstrip("/") == "/dictionary/english/resilience"},
        ),
        "arxiv": (
            "arXiv · real web",
            "https://arxiv.org/",
            "Find the arXiv abstract page for the paper 'Attention Is All You Need'.",
            lambda page, agent: {"Abstract 1706.03762": on(page, "arxiv.org")
                                 and bool(re.fullmatch(r"/abs/1706\.03762(v\d+)?", path(page)))},
        ),
        "github": (
            "GitHub · real web",
            "https://github.com/",
            "Open the GitHub repository of the browser-use project, owned by the browser-use organization.",
            lambda page, agent: {"browser-use/browser-use": on(page, "github.com")
                                 and path(page).rstrip("/").lower() == "/browser-use/browser-use"},
        ),
        "huggingface": (
            "Hugging Face · real web",
            "https://huggingface.co/",
            "Open the Hugging Face model page for bert-base-uncased.",
            lambda page, agent: {
                "bert-base-uncased model page": on(page, "huggingface.co")
                and path(page).rstrip("/") in {"/bert-base-uncased", "/google-bert/bert-base-uncased"}
            },
        ),
        "apple": (
            "Apple · real web",
            "https://www.apple.com/",
            "On Apple's website, open the page that compares iPhone models.",
            lambda page, agent: {"iPhone compare page": on(page, "apple.com")
                                 and path(page).rstrip("/").endswith("/iphone/compare")},
        ),
        "coursera": (
            "Coursera · real web",
            "https://www.coursera.org/",
            "Search Coursera for courses about machine learning.",
            lambda page, agent: {
                "Coursera search page": on(page, "coursera.org") and path(page).rstrip("/") == "/search",
                "Query is machine learning": parse_qs(urlparse(page["url"]).query).get("query", [""])[0].lower()
                == "machine learning",
            },
        ),
    }
    suites = {"core": core, "wildlife": wildlife, "reallife": reallife}
    return {name: Task(*task, suite=suite) for suite, group in suites.items() for name, task in group.items()}


def verify(check, agent):
    """Observe the final page afresh and run the task's checks."""
    checks = check(agent.browser.observe(screenshot=False), agent)
    return {"passed": all(checks.values()), "checks": checks}
