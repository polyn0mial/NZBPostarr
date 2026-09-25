"""The compiled stylesheet covers every class the markup uses.

Tailwind only emits the utilities it finds in its @source files, so a class the templates
use but the build never saw renders unstyled with no error anywhere. These tests read the
static class lists in the templates and the literal class strings in the page scripts and
check each one against the selectors the served stylesheets define. The committed baseline
is the class set compiled before the W3-F3 build rework; it must stay a subset.
"""

import re
from pathlib import Path

from tests.webui._source import REPO_ROOT

WEBUI = REPO_ROOT / "webui"
CSS_DIR = WEBUI / "assets" / "css"
BASELINE = Path(__file__).with_name("tailwind_classes_baseline.txt")

# Classes the markup carries as JS/DOM hooks or state markers: no stylesheet rule targets
# them on purpose (group/peer are Tailwind variant anchors that emit no rule of their own).
HOOK_CLASSES = frozenset(
    {
        "group",
        "group/header",
        "peer",
        "status-dot",
        "lucide-icon-wrapper",
        "console-scroll-target",
        "section-chevron",
        "expand-trigger-btn",
        "expand-trigger-icon",
        "queue-group",
        "queue-group-header",
        "queue-item",
        "drag-handle",
        "active-job-row",
        "active-job-drag-handle",
        "queued-job-row",
        "queued-job-drag-handle",
    }
)

# Already unstyled before W3-F3 (no notion-info / notion-bg-primary colour exists, and the
# animate-in family needs a plugin the build never had). Styling them changes pixels, so
# they are pinned here rather than fixed in a build-only batch; the list may only shrink.
KNOWN_UNSTYLED = frozenset(
    {
        "bg-notion-info/5",
        "bg-notion-info/10",
        "border-notion-info/30",
        "text-notion-info",
        "hover:bg-notion-info/10",
        "hover:bg-notion-info/15",
        "hover:bg-notion-info/20",
        "hover:border-notion-info/40",
        "hover:text-notion-info",
        "bg-notion-bg-primary",
        "animate-in",
        "fade-in",
        "zoom-in",
    }
)

_SELECTOR_CLASS = re.compile(r"\.((?:\\.|[A-Za-z0-9_-])+)")
_STATIC_CLASS_ATTR = re.compile(r'(?<![:\w-])(?:class|icon-class)="([^"]*)"')
_CLASS_TOKEN = re.compile(r"^!?-?[a-z][a-z0-9:/.\[\]()%#_!-]*$")
_TEMPLATE_TAG = re.compile(r"\{%.*?%\}")


_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_DECLARATIONS = re.compile(r"\{[^{}]*\}")
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.S)


def _compiled_classes(css: str) -> set[str]:
    """Class names in the selectors of a stylesheet. The innermost {...} blocks are the
    declarations; dropping them keeps numbers like .25rem out of the set."""
    selectors = _DECLARATIONS.sub("{}", _COMMENT.sub("", css))
    return {re.sub(r"\\(.)", r"\1", name) for name in _SELECTOR_CLASS.findall(selectors)}


def _templates() -> list[Path]:
    return sorted(WEBUI.glob("*.html")) + sorted((WEBUI / "partials").rglob("*.html"))


def _served_classes() -> set[str]:
    # base.html and login.html load core.css and the compiled tailwind.css; a few pages
    # add their own <style> block.
    sheets = [(CSS_DIR / name).read_text(encoding="utf-8") for name in ("tailwind.css", "core.css")]
    for path in _templates():
        sheets += _STYLE_BLOCK.findall(path.read_text(encoding="utf-8"))
    return set().union(*(_compiled_classes(css) for css in sheets))


def _template_candidates() -> dict[str, str]:
    """Every token of a static class="..." or icon-class="..." attribute, mapped to a file it
    appears in. Values holding bound template syntax are skipped: they are not literal. The
    page scripts' own template strings are scanned too."""
    found: dict[str, str] = {}
    sources = _templates() + [WEBUI / "assets" / "js" / "page-base.js"] + sorted((WEBUI / "assets" / "js" / "pages").rglob("*.js"))
    for path in sources:
        for value in _STATIC_CLASS_ATTR.findall(path.read_text(encoding="utf-8")):
            if "{{" in value or "[[" in value or "${" in value:
                continue
            for token in _TEMPLATE_TAG.sub(" ", value).split():
                if _CLASS_TOKEN.match(token):
                    found.setdefault(token, path.name)
    return found


def test_every_static_template_class_is_compiled() -> None:
    known = _served_classes() | HOOK_CLASSES | KNOWN_UNSTYLED
    missing = {token: where for token, where in _template_candidates().items() if token not in known}

    assert missing == {}, f"classes used in the markup but absent from every served stylesheet: {missing}"


def test_known_unstyled_classes_are_still_unstyled_and_used() -> None:
    # A stale entry would hide a regression: once styled or no longer used, drop it.
    served = _served_classes()
    candidates = _template_candidates()

    assert sorted(KNOWN_UNSTYLED & served) == []
    assert sorted(KNOWN_UNSTYLED - set(candidates)) == []


def test_template_scan_finds_the_markup() -> None:
    # Guards the scan itself: an extractor that silently matches nothing passes the test above.
    candidates = _template_candidates()

    assert len(candidates) > 300
    assert {"text-notion-text-primary", "dark:hidden", "size-4"} <= set(candidates)


def test_pre_rework_class_set_is_still_compiled() -> None:
    baseline = set(BASELINE.read_text(encoding="utf-8").split())
    compiled = _compiled_classes((CSS_DIR / "tailwind.css").read_text(encoding="utf-8"))

    assert len(baseline) > 600
    assert sorted(baseline - compiled) == []
