"""Show what our team changed in one item: stock LMA 0.3.2 ("before") vs the live stack.

Run from the repo root:

    python new_environment_export/tools/show_change.py --list           # everything you can ask for
    python new_environment_export/tools/show_change.py Query.getCall    # one AppSync resolver
    python new_environment_export/tools/show_change.py GetUserProjects  # one pipeline function (all new)
    python new_environment_export/tools/show_change.py schema
    python new_environment_export/tools/show_change.py vpscheduler
    python new_environment_export/tools/show_change.py meetingcontrols
    python new_environment_export/tools/show_change.py stepfunction

Lines starting with "-" are stock code that was removed, "+" lines are what we added.
"""
import difflib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPORT = os.path.dirname(HERE)
HISTORY = os.path.join(EXPORT, "history")
BEFORE_RESOLVERS = os.path.join(HISTORY, "appsync_current", "resolvers")
STOCK_RESOLVERS = os.path.join(HISTORY, "stock_0.3.2", "resolvers.txt")
LIVE_RESOLVERS = os.path.join(EXPORT, "appsync", "resolvers")
LIVE_FUNCTIONS = os.path.join(EXPORT, "appsync", "functions")
CODE_FIELDS = ["kind", "dataSourceName", "pipelineConfig", "runtime",
               "requestMappingTemplate", "responseMappingTemplate", "code"]

SCHEMA_PAIR = (os.path.join(HISTORY, "stock_0.3.2", "schema.graphql"),
               os.path.join(EXPORT, "appsync", "schema.graphql"))
FILE_PAIRS = {
    "vpscheduler": (os.path.join(HISTORY, "vp_fix_backups", "vpsched-backup_src", "index.py"),
                    os.path.join(EXPORT, "lambda", "LMASA-VPScheduler_src", "index.py")),
    "meetingcontrols": (os.path.join(HISTORY, "stock_0.3.2", "meeting_controls_resolver_index.py"),
                        os.path.join(EXPORT, "lambda", "LMASA-MeetingControlsLambda_src", "index.py")),
}


def read(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().replace("\r", "")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def show_text_diff(before, after, label_a, label_b):
    diff = list(difflib.unified_diff(before.splitlines(), after.splitlines(), label_a, label_b, lineterm="", n=3))
    print("\n".join(diff) if diff else "(no difference)")


def stock_resolvers():
    with open(STOCK_RESOLVERS, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def before_of(name):
    """Stock version of a resolver. The 07-08 snapshot equals stock 0.3.2 for every resolver
    that exists in 0.3.2; anything not in 0.3.2 counts as new."""
    path = os.path.join(BEFORE_RESOLVERS, name + ".json")
    if name not in stock_resolvers() or not os.path.exists(path):
        return {}
    return load_json(path)


def parse_schema(path):
    """'type Name' -> {field: signature}, ignoring comments and layout."""
    text = re.sub(r"#[^\n]*", "", read(path))
    out = {}
    for m in re.finditer(r"\b(type|input|enum|interface|union)\s+(\w+)[^{]*\{([^}]*)\}", text):
        fields = {}
        for fm in re.finditer(r"(\w+)\s*(\([^)]*\))?\s*:\s*([\w\[\]!]+)", m.group(3)):
            args = ", ".join(sorted(a.strip() for a in (fm.group(2) or "").strip("()").split(",") if a.strip()))
            fields[fm.group(1)] = (f"({args})" if args else "") + ": " + fm.group(3)
        out[f"{m.group(1)} {m.group(2)}"] = fields
    return out


def show_schema():
    a, b = parse_schema(SCHEMA_PAIR[0]), parse_schema(SCHEMA_PAIR[1])
    print("Schema additions vs stock LMA 0.3.2 (layout differences ignored):\n")
    for t in sorted(set(b) - set(a)):
        print(f"  + {t} {{ {', '.join(f + s for f, s in b[t].items())} }}")
    for t in sorted(set(a) & set(b)):
        for f in sorted(set(b[t]) - set(a[t])):
            print(f"  + {t}.{f}{b[t][f]}")
        for f in sorted(set(a[t]) - set(b[t])):
            print(f"  - {t}.{f}")
        for f in sorted(set(a[t]) & set(b[t])):
            if a[t][f] != b[t][f]:
                print(f"  ~ {t}.{f}: {a[t][f]}  ->  {b[t][f]}")


def show_resolver(name):
    live_path = os.path.join(LIVE_RESOLVERS, name + ".json")
    if not os.path.exists(live_path):
        sys.exit(f"No resolver or function called {name}. Try --list.")
    live = load_json(live_path)
    before = before_of(name)
    if not before:
        print(f"{name} is NEW - it does not exist in stock LMA 0.3.2.\n")
    pipeline = (live.get("pipelineConfig") or {}).get("functions", [])
    if pipeline:
        index = {f["functionId"]: f["name"] for f in load_json(os.path.join(EXPORT, "appsync", "_functions_index.json"))}
        print("Pipeline (runs in this order): " + " -> ".join(index.get(i, i) for i in pipeline) + "\n")
    for field in CODE_FIELDS:
        a, b = before.get(field), live.get(field)
        if a == b:
            continue
        print(f"===== {field}")
        if isinstance(a, str) or isinstance(b, str):
            show_text_diff(a or "", b or "", "stock-0.3.2", "live")
        else:
            print(f"  stock-0.3.2: {json.dumps(a)}\n  live:        {json.dumps(b)}")
        print()


def show_function(name):
    f = load_json(os.path.join(LIVE_FUNCTIONS, name + ".json"))
    print(f"{name} is a NEW pipeline function (stock LMA 0.3.2 has none).")
    print(f"Data source: {f.get('dataSourceName')}   Runtime: {(f.get('runtime') or {}).get('name', 'VTL')}\n")
    for field in ("code", "requestMappingTemplate", "responseMappingTemplate"):
        if f.get(field):
            print(f"===== {field}\n{f[field]}\n")


def show_stepfunction():
    before = load_json(os.path.join(HISTORY, "vp_fix_backups", "sfn-definition-backup.json"))
    live = load_json(os.path.join(EXPORT, "stepfunction", "definition_only.json"))
    if isinstance(before, dict) and "definition" in before:
        before = json.loads(before["definition"])
    show_text_diff(json.dumps(before, indent=2, sort_keys=True), json.dumps(live, indent=2, sort_keys=True),
                   "before (07-20 backup = stock + task def :3)", "live")


def list_all():
    print("Resolvers we changed or added:")
    for fn in sorted(os.listdir(LIVE_RESOLVERS)):
        name = fn[:-5]
        a = before_of(name)
        if not a:
            print(f"  NEW      {name}")
            continue
        b = load_json(os.path.join(LIVE_RESOLVERS, fn))
        if any(a.get(k) != b.get(k) for k in CODE_FIELDS):
            print(f"  CHANGED  {name}")
    print("\nPipeline functions (all new):")
    print("  " + ", ".join(sorted(f[:-5] for f in os.listdir(LIVE_FUNCTIONS))))
    print("\nOther items: " + ", ".join(["schema"] + list(FILE_PAIRS) + ["stepfunction"]))


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return
    arg = sys.argv[1]
    if arg == "--list":
        list_all()
    elif arg == "schema":
        show_schema()
    elif arg in FILE_PAIRS:
        a, b = FILE_PAIRS[arg]
        show_text_diff(read(a), read(b), "stock-0.3.2", "live")
    elif arg == "stepfunction":
        show_stepfunction()
    elif os.path.exists(os.path.join(LIVE_FUNCTIONS, arg + ".json")):
        show_function(arg)
    else:
        show_resolver(arg)


if __name__ == "__main__":
    main()
