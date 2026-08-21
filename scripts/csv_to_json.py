#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path
import pandas as pd

# COLUMN POSITIONS
FOOD_CODE_INDEX = 0
FOOD_NAME_INDEX = 1
PROMPT_TYPE_INDEX = 2
PROMPT_NAME_INDEX = 3
PROMPT_TEXT_INDEX = 4
PROMPT_DESCRIPTION_INDEX = 5
OPTION_LABEL_INDEX = 6
OPTION_SHORT_LABEL_INDEX = 7
REPLACEMENT_FOOD_CODE_INDEX = 8
PROMPT_COLUMN_COUNT = 7


def text(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none"} else s


def code(v):
    s = text(v)
    return re.sub(r"\.0$", "", s) if re.fullmatch(r"\d+\.0", s) else s


def slug(s):
    s = text(s).lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def prompt_column_index(index, level):
    return index + level * PROMPT_COLUMN_COUNT


def col(row, index, level):
    column_index = prompt_column_index(index, level)
    return text(row[column_index]) if column_index < len(row) else ""


def prompt_starts(rows, level):
    return [
        i for i, row in enumerate(rows)
        if col(row, PROMPT_TYPE_INDEX, level)
    ]

def prompt_components(type):
    match type:
        case "select":
            return "select-prompt"
        case "yes-no":
            return "yes-no-prompt"
        case "radio":
            return "radio-list-prompt"
        case _:
            return "component-not-matched"

def parse_prompts(rows, level):
    """Return prompt nodes at one column level."""
    starts = prompt_starts(rows, level)
    nodes = []

    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(rows)
        first = rows[start]
        options = []

        for i in range(start, end):
            label = col(rows[i], OPTION_LABEL_INDEX, level)
            if label:
                short = (
                    col(rows[i], OPTION_SHORT_LABEL_INDEX, level) or label
                )
                options.append({
                    "row": i,
                    "label": label,
                    "short": short,
                    "replacement": code(
                        col(rows[i], REPLACEMENT_FOOD_CODE_INDEX, level)
                    ),
                })

        nodes.append({
            "start": start,
            "end": end,
            "component": prompt_components(
                col(first, PROMPT_TYPE_INDEX, level)
            ),
            "name": col(first, PROMPT_NAME_INDEX, level),
            "text": col(first, PROMPT_TEXT_INDEX, level),
            "description": col(first, PROMPT_DESCRIPTION_INDEX, level),
            "options": options,
        })

    return nodes


def child_prompt(rows, option, option_end, child_level):
    """Find the child prompt belonging to one option's row range."""
    local_starts = prompt_starts(
        rows[option["row"]:option_end], child_level
    )
    if not local_starts:
        return None

    wanted = option["row"] + local_starts[0]
    for node in parse_prompts(rows, child_level):
        if node["start"] == wanted:
            return node
    return None


def make_id(food_name, level, path, prompt_name):
    letter = chr(ord("A") + level)
    parts = [slug(x) for x in path + [prompt_name] if slug(x)]
    return f"{slug(food_name)}-{letter}-{'-'.join(parts)}"


def make_display_name(food_name, path, prompt_name):
    return (
        text(food_name)
        + "".join(f" ({p})" for p in path)
        + f" {prompt_name}"
    ).strip()


def condition(food_code, parent_prompt_id=None, parent_value=None):
    if parent_prompt_id is None:
        return {
            "object": "food",
            "orPrevious": False,
            "property": {
                "id": "tag",
                "type": "tag",
                "check": {"tagId": food_code, "value": True},
            },
        }

    return {
        "object": "food",
        "orPrevious": False,
        "property": {
            "id": "promptAnswer",
            "type": "promptAnswer",
            "check": {
                "op": "eq",
                "value": parent_value,
                "promptId": parent_prompt_id,
                "required": True,
            },
        },
    }


def make_prompt(node, food_name, food_code, level, path,
                parent_prompt_id=None, parent_value=None):
    prompt = {
        "id": make_id(food_name, level, path, node["name"]),
        "name": make_display_name(food_name, path, node["name"]),
        "version": 4,
        "i18n": {
            "name": {"en": node["name"]},
            "text": {"en": node["text"]},
            "description": {"en": f"<p>{node['description']}</p>"},
        },
        "conditions": [
            condition(
                food_code, parent_prompt_id, parent_value
            )
        ],
        "useGraph": False,
        "type": "custom",
        "validation": {"required": True, "message": {}},
        "component": node["component"],
    }

    comp = node["component"]
    if comp == "radio-list-prompt" or comp == "select-prompt":
        prompt["other"] = False
        prompt["options"] = {"en": []}
        for option in node["options"]:
            item = {
                "label": option["label"],
                "shortLabel": option["short"],
                "value": option["short"],
            }

            if option["replacement"]:
                item["action"] = {
                    "type": "updateFood",
                    "params": {"code": option["replacement"]},
                }

            prompt["options"]["en"].append(item)

        if comp == "radio-list-prompt":
            prompt["orientation"] = "column"
        if comp == "select-prompt":
            prompt["multiple"] = False

    if comp == "yes-no-prompt":
        prompt["useFlag"] = False
        yes_option = node["options"][0]["replacement"]
        no_option = node["options"][1]["replacement"]
        prompt["flag"] = "yes"
        if yes_option:
          prompt["trueAction"] = {
              "type": "updateFood",
              "params": {"code": yes_option},
          }
        if no_option:
          prompt["falseAction"] = {
              "type": "updateFood",
              "params": {"code": no_option},
          }

    return prompt


def build_tree(rows, node, food_name, food_code, level, path,
               parent_prompt_id, parent_value, output):
    """Append a prompt, then recursively append its conditional children."""
    prompt = make_prompt(
        node, food_name, food_code, level, path,
        parent_prompt_id, parent_value
    )
    output.append(prompt)

    for n, option in enumerate(node["options"]):
        option_end = (
            node["options"][n + 1]["row"]
            if n + 1 < len(node["options"])
            else node["end"]
        )

        child = child_prompt(rows, option, option_end, level + 1)
        if child:
            build_tree(
                rows,
                child,
                food_name,
                food_code,
                level + 1,
                path + [option["short"]],
                prompt["id"],
                ("true" if n == 0 else "false")
                if node["component"] == "yes-no-prompt"
                else option["short"],
                output,
            )


def convert(input_csv):
    try:
        df = pd.read_csv(input_csv, encoding="utf-8-sig", dtype=str).fillna("")
    except UnicodeDecodeError:
        df = pd.read_csv(input_csv, encoding="cp1252", dtype=str).fillna("")

    rows = df.values.tolist()
    starts = [
        i for i, row in enumerate(rows)
        if text(row[FOOD_CODE_INDEX])
    ]

    output = []

    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(rows)
        food_rows = rows[start:end]

        food_code = code(food_rows[0][FOOD_CODE_INDEX])
        food_name = text(food_rows[0][FOOD_NAME_INDEX])

        roots = parse_prompts(food_rows, 0)

        for root in roots:
            build_tree(
                food_rows, root, food_name, food_code,
                0, [], None, None, output
            )

    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("output_json", type=Path)
    args = parser.parse_args()

    prompts = convert(args.input_csv)

    with args.output_json.open("w", encoding="utf-8") as f:
        json.dump(prompts, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(prompts)} prompts to {args.output_json}")


if __name__ == "__main__":
    main()
