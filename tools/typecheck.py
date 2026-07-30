#!/usr/bin/env python3
"""
Static type check for GDScript calls to functions declared in this project.

This exists because of a real bug that reached the user: `_add_box()` was
changed to take a `Vector3` rotation, but one of the edits that was supposed to
update the three call sites silently did not apply. The project still parsed
and still linted cleanly - `gdparse` only checks syntax and `gdlint` only checks
style - so nothing caught it until Godot refused to run:

    Cannot pass a value of type "float" as "Vector3".

This walks every .gd file, records the typed signature of each `func`, then
checks every call to those functions for:

  * wrong number of arguments (respecting default values)
  * an argument whose type can be inferred and does not match the parameter

Only literals and obvious constructors are inferred, so this is deliberately
conservative: it reports something only when it is confident. That is enough to
catch the class of mistake above.

Run:  python3 tools/typecheck.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBLEMS = []

# Godot types that a numeric literal is NOT compatible with.
VECTOR_TYPES = {
    "Vector2", "Vector2i", "Vector3", "Vector3i", "Vector4", "Vector4i",
    "Color", "Transform2D", "Transform3D", "Basis", "Quaternion", "Plane",
    "AABB", "Rect2", "Rect2i", "NodePath", "StringName", "String",
}
NUMERIC_TYPES = {"float", "int"}


def split_args(text):
    """Split a call's argument list on top-level commas."""
    args, depth, cur = [], 0, ""
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def infer_type(expr, locals_map=None):
    """Best-effort type of an expression. None means 'cannot tell'."""
    e = expr.strip()
    if not e:
        return None

    # a local variable whose type we recorded from its declaration
    if locals_map and re.match(r"^\w+$", e) and e in locals_map:
        return locals_map[e]

    # constructor call, e.g. Vector3(...), Color(...)
    m = re.match(r"^([A-Z]\w*)\s*\(", e)
    if m and m.group(1) in VECTOR_TYPES:
        return m.group(1)

    # constant, e.g. Vector3.ZERO, Color.RED
    m = re.match(r"^([A-Z]\w*)\.[A-Z_][A-Z0-9_]*$", e)
    if m and m.group(1) in VECTOR_TYPES:
        return m.group(1)

    # plain numeric literal
    if re.match(r"^-?\d+\.\d+$", e):
        return "float"
    if re.match(r"^-?\d+$", e):
        return "int"

    # numeric built-ins that always return a float
    if re.match(r"^(deg_to_rad|rad_to_deg|sqrt|sin|cos|tan|atan|atan2|absf|"
                r"maxf|minf|lerpf|clampf|signf|floorf|ceilf|pow|fmod)\s*\(", e):
        return "float"

    # arithmetic over locals/constants that are all numeric, e.g. "TAU * i / 6.0"
    if locals_map is not None and re.match(r"^[\w\s.+\-*/()]+$", e):
        names = set(re.findall(r"[A-Za-z_]\w*", e))
        if names:
            known = {"TAU", "PI", "INF"}
            types = []
            for n in names:
                if n in known:
                    types.append("float")
                elif n in locals_map:
                    types.append(locals_map[n])
                else:
                    types = None
                    break
            if types and all(t in NUMERIC_TYPES for t in types):
                return "float" if ("." in e or "/" in e
                                   or "float" in types) else "int"

    # a bare arithmetic expression of literals, e.g. "8.0 + i * 7.0"
    if re.match(r"^[\d\.\s+\-*/()]+$", e) and re.search(r"\d", e):
        return "float" if "." in e else "int"

    return None


def compatible(actual, declared):
    """Is a value of type `actual` acceptable where `declared` is expected?"""
    if actual is None or declared is None:
        return True
    if actual == declared:
        return True
    if actual in NUMERIC_TYPES and declared in NUMERIC_TYPES:
        return True          # int widens to float, and Godot accepts float->int
    if actual in NUMERIC_TYPES and declared in VECTOR_TYPES:
        return False         # the bug this tool exists for
    if actual in VECTOR_TYPES and declared in NUMERIC_TYPES:
        return False
    if actual in VECTOR_TYPES and declared in VECTOR_TYPES:
        return actual == declared
    return True


def collect_signatures(sources):
    """{function name: [(param name, type, has_default), ...]}"""
    sigs = {}
    for path, text in sources.items():
        # a func header may wrap over several lines before the closing paren
        for m in re.finditer(r"^func\s+(\w+)\s*\((.*?)\)\s*(?:->\s*[\w\[\], ]+)?:",
                             text, re.S | re.M):
            name, raw = m.group(1), m.group(2)
            params = []
            for p in split_args(raw):
                if not p or p.startswith("."):
                    continue
                has_default = "=" in p
                decl = p.split("=")[0].strip()
                if ":" in decl:
                    pname, ptype = decl.split(":", 1)
                    params.append((pname.strip(), ptype.strip(), has_default))
                else:
                    params.append((decl, None, has_default))
            sigs[name] = params
    return sigs


def collect_locals(text):
    """{variable name: type} for typed or obviously-inferable declarations."""
    out = {}
    # var x: Type = ...   /   var x: Type
    for m in re.finditer(r"\bvar\s+(\w+)\s*:\s*([\w\[\], ]+?)\s*(?:=|$)",
                         text, re.M):
        out[m.group(1)] = m.group(2).strip()
    # loop counters: "for i in 3:" is an int. Recorded before the := pass so
    # that expressions using them, e.g. "var a := TAU * i / 6.0", resolve.
    for m in re.finditer(r"\bfor\s+(\w+)\s+in\s+\d+\s*:", text):
        out.setdefault(m.group(1), "int")
    # var x := <expr>, repeated so declarations can depend on earlier ones
    for _ in range(3):
        grew = False
        for m in re.finditer(r"\bvar\s+(\w+)\s*:=\s*(.+)$", text, re.M):
            if m.group(1) in out:
                continue
            t = infer_type(m.group(2).strip(), out)
            if t:
                out[m.group(1)] = t
                grew = True
        if not grew:
            break
    return out


def check_calls(sources, sigs):
    for path, text in sources.items():
        # strip comments and strings so they cannot look like calls
        clean_lines = []
        for line in text.splitlines():
            line = re.sub(r'"[^"]*"', '""', line)
            clean_lines.append(line.split("#")[0])
        clean = "\n".join(clean_lines)
        local_types = collect_locals(clean)

        for fname, params in sigs.items():
            for m in re.finditer(r"(?<![\w.])" + re.escape(fname) + r"\s*\(", clean):
                # skip the declaration itself
                before = clean[max(0, m.start() - 6):m.start()]
                if before.rstrip().endswith("func"):
                    continue

                # find the matching close paren
                i = m.end() - 1
                depth, j = 0, i
                while j < len(clean):
                    if clean[j] in "([{":
                        depth += 1
                    elif clean[j] in ")]}":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                args = split_args(clean[i + 1:j])
                line_no = clean[:m.start()].count("\n") + 1

                required = sum(1 for _n, _t, d in params if not d)
                if len(args) < required or len(args) > len(params):
                    PROBLEMS.append(
                        "%s:%d: %s() takes %d-%d arguments, %d given"
                        % (os.path.basename(path), line_no, fname,
                           required, len(params), len(args)))
                    continue

                for (pname, ptype, _d), arg in zip(params, args):
                    actual = infer_type(arg, local_types)
                    if not compatible(actual, ptype):
                        PROBLEMS.append(
                            '%s:%d: %s() parameter "%s" expects %s, '
                            'but got %s  ->  %s'
                            % (os.path.basename(path), line_no, fname,
                               pname, ptype, actual, arg))


def main():
    scripts = os.path.join(ROOT, "scripts")
    sources = {}
    for name in sorted(os.listdir(scripts)):
        if name.endswith(".gd"):
            sources[os.path.join(scripts, name)] = open(os.path.join(scripts, name)).read()

    sigs = collect_signatures(sources)
    print("GDScript call type check")
    print("  %d scripts, %d typed functions" % (len(sources), len(sigs)))
    check_calls(sources, sigs)

    if PROBLEMS:
        print("\n%d problem(s) found:" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  " + p)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
