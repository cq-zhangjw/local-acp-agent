import traceback
from typing import Any, Dict

import MeCab
import ipadic
import jaconv
from fastmcp import FastMCP

mcp = FastMCP("JP Assistant Server")

# Initialize MeCab tagger with ipadic dictionary
mecab = MeCab.Tagger(ipadic.MECAB_ARGS)


def _tokenize(text: str) -> list[tuple[str, list[str]]]:
    """MeCab parse -> [(surface, feature_fields), ...] until EOS."""
    words: list[tuple[str, list[str]]] = []
    for line in mecab.parse(text).split("\n"):
        if line in ("", "EOS"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        words.append((parts[0], parts[1].split(",")))
    return words


def _reading_of(feature: list[str], surface: str) -> str:
    """Extract reading (second-to-last field); fall back to surface when missing."""
    hira = feature[-2]
    if hira == "*" or hira == surface:
        return ""
    return jaconv.kata2hira(hira)


@mcp.tool()
def jp_to_hiragana(text: str) -> str:
    """
    Convert Japanese text to hiragana (平仮名).

    Args:
        text: Input Japanese text (may contain kanji, katakana, etc.)

    Returns:
        str: Text with convertible characters converted to hiragana
    """
    try:
        result = ""
        for surface, feature in _tokenize(text):
            hira = feature[-2]
            if hira == "*" or hira == surface:
                result += surface
            else:
                result += jaconv.kata2hira(hira)
        return result
    except:  # noqa: BLE001
        return f"Error: {traceback.format_exc()}"


@mcp.tool()
def jp_to_katakana(text: str) -> str:
    """
    Convert Japanese text to katakana (片仮名).

    Args:
        text: Input Japanese text (may contain kanji, hiragana, etc.)

    Returns:
        str: Text with convertible characters converted to katakana
    """
    try:
        result = ""
        for surface, feature in _tokenize(text):
            hira = feature[-2]
            if hira == "*" or hira == surface:
                result += surface
            else:
                result += jaconv.hira2kata(hira)
        return result
    except:  # noqa: BLE001
        return f"Error: {traceback.format_exc()}"


@mcp.tool()
def jp_analyze_words(text: str) -> Dict[str, Dict[str, str]]:
    """
    Analyze Japanese text and return word details (word -> reading & POS).

    Args:
        text: Input Japanese text to analyze

    Returns:
        dict: Each key is a surface word; value contains "kana" (hiragana reading),
              "hira" (katakana reading) and "category" (part of speech)
    """
    try:
        result_dict: Dict[str, Dict[str, str]] = {}
        for surface, feature in _tokenize(text):
            if surface in result_dict:
                continue
            hira = feature[-2]
            if hira == "*" or hira == surface:
                result_dict[surface] = {"kana": "", "hira": "", "category": ""}
            else:
                kana = jaconv.kata2hira(hira)
                category = ",".join(feature[:-3]).replace(",*", "").rstrip(",")
                result_dict[surface] = {"kana": kana, "hira": hira, "category": category}
        return result_dict
    except:  # noqa: BLE001
        return {"error": traceback.format_exc()}


@mcp.tool()
def jp_mark_ruby(text: str) -> str:
    """
    Add hiragana reading annotations to Japanese text as HTML <ruby> tags.

    Args:
        text: Input Japanese text (kanji will be annotated)

    Returns:
        str: HTML fragment like <ruby>中国<rt>ちゅうごく</rt></ruby>の<ruby>重慶<rt>じゅうけい</rt></ruby>
    """
    try:
        result = ""
        for surface, feature in _tokenize(text):
            hira = feature[-2]
            if hira == "*" or hira == surface:
                result += surface
            else:
                kana = jaconv.kata2hira(hira)
                result += f"<ruby>{surface}<rt>{kana}</rt></ruby>" if surface != kana else surface
        return result
    except:  # noqa: BLE001
        return f"Error: {traceback.format_exc()}"


@mcp.tool()
def jp_mark_bracket(text: str) -> str:
    """
    Add hiragana reading annotations to Japanese text as 漢字[かな] brackets.

    Args:
        text: Input Japanese text (kanji will be annotated)

    Returns:
        str: Text like 中国[ちゅうごく]の重慶[じゅうけい]
    """
    try:
        result = ""
        for surface, feature in _tokenize(text):
            hira = feature[-2]
            if hira == "*" or hira == surface:
                result += surface
            else:
                kana = jaconv.kata2hira(hira)
                result += f"{surface}[{kana}] " if surface != kana else surface
        return result
    except:  # noqa: BLE001
        return f"Error: {traceback.format_exc()}"


if __name__ == "__main__":
    import sys

    sys.stdin = open(sys.stdin.fileno(), "r", encoding="utf-8", closefd=False)
    sys.stdout = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)
    mcp.run(transport="stdio")
