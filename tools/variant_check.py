#!/usr/bin/env python3
"""
Find `:=` declarations whose initialiser is a Variant, which Godot refuses to
compile.

WHY THIS EXISTS
---------------

This shipped to the user in v3.7 and stopped the whole project from running:

    res://scripts/damage.gd:496 - Parse Error: Cannot infer the type of
    "is_wheel" variable because the value doesn't have a set type.
    res://scripts/vehicle.gd:-1 - Compile Error:
    res://scripts/game_settings.gd:-1 - Compile Error:

One bad line in one script took down every script that referenced it, which is
why the log looked catastrophic. The offending code was:

    for part in hits:                     # hits is a Dictionary
        var is_wheel := part in [...]     # <- Variant

Two separate Variant sources meet on that line.

  1. The loop variable of `for x in <Dictionary>` is a Variant. Dictionary keys
     are untyped, so GDScript cannot give `part` a static type.

  2. `in` compiles to Variant::OP_IN, and gdscript_analyzer.cpp:2956 says:

         } else if (left_type.is_variant() || right_type.is_variant()) {
             // Cannot infer type because one operand can be anything.
             result.kind = GDScriptParser::DataType::VARIANT;

     so the result of the comparison is a Variant too.

Then gdscript_analyzer.cpp:1929 fires:

    if (!initializer_type.is_set() || initializer_type.has_no_type()
            || !initializer_type.is_hard_type()) {
        push_error(vformat(R"(Cannot infer the type of "%s" %s because the
            value doesn't have a set type.)", ...));

Note what did NOT catch this: `gdlint` checks style, `gdparse` checks syntax,
and tools/typecheck.py checks call signatures. None of them models types, so
all three passed on a script the engine would not load. Hence this file.

WHAT IT CHECKS
--------------

For every `var name := expr`, it decides whether `expr` is a Variant:

  * a loop variable iterating a Dictionary                  -> Variant
  * indexing a Dictionary                                   -> Variant
  * any `in` / `not in` expression touching a Variant       -> Variant
  * a bare Variant loop variable used as the whole value    -> Variant

and reports the line. Conversions - int(x), float(x), String(x), a typed
`as Node` cast, or comparison operators that always yield bool - clear it,
because those genuinely do have a set type.

Dictionaries are recognised from `const NAME := {`, `var name := {`,
`var name: Dictionary`, and functions declared `-> Dictionary`.

Run:  python3 tools/variant_check.py
"""

import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Wrapping a Variant in one of these gives it a set type again.
CONVERSIONS = (
    "int", "float", "bool", "String", "StringName", "NodePath",
    "Vector2", "Vector3", "Vector4", "Color", "str", "len",
    "absi", "absf", "clampf", "clampi", "maxf", "minf", "maxi", "mini",
    "signf", "signi", "roundi", "floori", "ceili", "snappedf",
)

# Operators whose result is always bool no matter what the operands are.
BOOL_OPS = ("==", "!=", "<", ">", "<=", ">=", " and ", " or ", "not ")


def dictionary_names(text):
    """Names in this file that hold a Dictionary."""
    names = set()
    names |= set(re.findall(r"^(?:const|var)\s+(\w+)\s*:=\s*\{", text, re.M))
    names |= set(re.findall(r"^\s*(?:const|var)\s+(\w+)\s*:\s*Dictionary",
                            text, re.M))
    names |= set(re.findall(r"^\s*var\s+(\w+)\s*:=\s*\{", text, re.M))
    return names


def dictionary_funcs(text):
    """Functions in this file that return a Dictionary."""
    return set(re.findall(r"^\s*(?:static\s+)?func\s+(\w+)\s*\(.*?\)\s*->\s*"
                          r"Dictionary", text, re.M))


def is_dictionary_expr(expr, dicts, dfuncs):
    """Does this expression evaluate to a Dictionary?"""
    e = expr.strip()
    if e in dicts:
        return True
    # SomeClass.CONST_DICT
    m = re.match(r"^\w+\.(\w+)$", e)
    if m and m.group(1) in dicts:
        return True
    # a call to a function returning Dictionary, possibly qualified
    m = re.match(r"^(?:\w+\.)?(\w+)\s*\(", e)
    if m and m.group(1) in dfuncs:
        return True
    return False


def has_set_type(expr):
    """True when the expression's type is fixed regardless of its operands."""
    e = expr.strip()
    m = re.match(r"^(\w+)\s*\(", e)
    if m and m.group(1) in CONVERSIONS:
        return True
    if re.search(r"\bas\s+[A-Z]\w*\s*$", e):
        return True
    for op in BOOL_OPS:
        if op in e:
            # `in` still poisons a bool-looking expression, so check it last.
            if not re.search(r"(^|\s)(not\s+)?in\s", e):
                return True
    return False


def check_file(path, problems, global_dicts, global_dfuncs):
    text = open(path, encoding="utf-8").read()
    # Dictionary-valued names and functions from EVERY script, not just this
    # one. The bug that shipped crossed files: the loop was in damage.gd and
    # the `-> Dictionary` function it iterated was in damage_model.gd, so a
    # single-file scan saw nothing and passed.
    dicts = dictionary_names(text) | global_dicts
    dfuncs = dictionary_funcs(text) | global_dfuncs
    rel = os.path.relpath(path, ROOT)

    # Local names holding a Dictionary, e.g. `var hits := parts_hit(...)`.
    for m in re.finditer(r"^\s*var\s+(\w+)\s*:=\s*(.+?)\s*$", text, re.M):
        if is_dictionary_expr(m.group(2), dicts, dfuncs):
            dicts.add(m.group(1))

    # Names currently holding a Variant, with the indent they are valid at.
    # Variant-ness propagates: a loop variable over a Dictionary is a Variant,
    # and so is anything assigned from it with an untyped `=`. The bug that
    # shipped hid behind exactly one such hop.
    variant_loops = []

    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.expandtabs(4)
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        variant_loops = [(i, v) for i, v in variant_loops if i < indent]

        m = re.match(r"^for\s+(\w+)\s+in\s+(.+):\s*$", line.strip())
        if m:
            if is_dictionary_expr(m.group(2), dicts, dfuncs):
                variant_loops.append((indent, m.group(1)))
            continue

        # Untyped assignment: `var x = <something Variant>` stays a Variant
        # and can poison a later `:=`.
        m = re.match(r"^var\s+(\w+)\s*=\s*(.+?)\s*$", line.strip())
        if m and not line.strip().startswith("var %s :=" % m.group(1)):
            expr = m.group(2)
            if not has_set_type(expr):
                tainted = any(
                    re.search(r"\b%s\b" % re.escape(v), expr)
                    for _, v in variant_loops)
                m2 = re.match(r"^(\w+)\[", expr)
                if tainted or (m2 and m2.group(1) in dicts) or \
                        is_dictionary_expr(expr, dicts, dfuncs):
                    # Recorded one level shallower than the line it is on: a
                    # local stays visible to its SIBLINGS, whereas a loop
                    # variable is only visible to the deeper-indented body.
                    # Filing both at the same depth silently dropped the
                    # local again on the very next line.
                    variant_loops.append((indent - 1, m.group(1)))
            continue

        m = re.match(r"^var\s+(\w+)\s*:=\s*(.+?)\s*$", line.strip())
        if not m:
            continue
        name, expr = m.group(1), m.group(2)
        if has_set_type(expr):
            continue

        # Indexing a Dictionary yields a Variant whatever the key's type is,
        # so this one does not depend on any loop variable being involved.
        m2 = re.match(r"^(\w+)\[", expr)
        if m2 and m2.group(1) in dicts:
            problems.append(
                "%s:%d: `var %s := %s` indexes a Dictionary, which yields a "
                "Variant. Give it an explicit type (`var %s: float = ...`) "
                "or wrap it in float()/int()/String()."
                % (rel, number, name, expr, name))
            continue

        loop_vars = [v for _, v in variant_loops]

        # `in` with a Variant operand -> Variant result.
        if re.search(r"(^|\s)(not\s+)?in\s", expr):
            touched = [v for v in loop_vars
                       if re.search(r"\b%s\b" % re.escape(v), expr)]
            if touched:
                problems.append(
                    "%s:%d: `var %s := ... in ...` is a Variant "
                    "(loop variable `%s` over a Dictionary). "
                    "Use a typed helper function."
                    % (rel, number, name, touched[0]))
                continue

        # The loop variable used bare, or indexed straight out of a dict.
        for v in loop_vars:
            if expr.strip() == v:
                problems.append(
                    "%s:%d: `var %s := %s` copies a Variant loop variable. "
                    "Wrap it, e.g. int(%s)." % (rel, number, name, v, v))
                break
            m2 = re.match(r"^(\w+)\[", expr)
            if m2 and m2.group(1) in dicts and \
                    re.search(r"\b%s\b" % re.escape(v), expr):
                problems.append(
                    "%s:%d: `var %s := %s` indexes a Dictionary, which "
                    "yields a Variant. Wrap it in float()/int()/String()."
                    % (rel, number, name, expr))
                break


def main():
    problems = []
    files = sorted(glob.glob(os.path.join(ROOT, "scripts", "*.gd")))

    # First pass: collect Dictionary names and Dictionary-returning functions
    # across the whole project, so cross-file cases are visible.
    global_dicts = set()
    global_dfuncs = set()
    for path in files:
        text = open(path, encoding="utf-8").read()
        global_dicts |= dictionary_names(text)
        global_dfuncs |= dictionary_funcs(text)

    for path in files:
        check_file(path, problems, global_dicts, global_dfuncs)

    print("Variant inference check")
    print("  %d scripts" % len(files))

    if problems:
        for p in problems:
            print("  FAIL " + p)
        print("\n%d PROBLEM(S)" % len(problems))
        return 1

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
