#!/usr/bin/env python3
"""
Catch the GDScript warnings Godot prints at startup.

WHY THIS EXISTS
---------------

The v3.8 log had four warnings that no existing check caught, because gdlint
checks style and gdparse checks syntax - neither models scope:

    SHADOWED_VARIABLE_BASE_CLASS  damage_model.gd:410
      The local function parameter "reference" is shadowing an
      already-declared method at the base class "RefCounted".

    SHADOWED_VARIABLE             damage.gd:397
      The local variable "coolant_fraction" is shadowing an
      already-declared function at line 708.

    UNUSED_PRIVATE_CLASS_VARIABLE vehicle.gd:179, 180, 181
      The class variable "_stuck_timer" is declared but never used.

The last three were leftovers: the timers moved to vehicle_recovery.gd when
that file was split out, and the originals were never deleted. Dead state that
looks live is worse than no state - the next person, me, reads it and assumes
the car still tracks being stuck here.

WHAT IT CHECKS

  1. SHADOWED_VARIABLE_BASE_CLASS - a local, parameter or class variable named
     the same as a member of a base class. Names come from the engine sources
     via tools/godot_api.json, which now covers Node, Node3D, CanvasItem,
     Object and RefCounted. `reference` lives on RefCounted, and RefCounted
     was the class missing from that file, which is precisely why it slipped
     through.

  2. SHADOWED_VARIABLE - a variable named the same as a function in the same
     script.

  3. UNUSED_PRIVATE_CLASS_VARIABLE - an `_underscore` class variable that is
     never read or written anywhere else in its file.

Run:  python3 tools/warning_check.py
"""

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Which engine class each script actually extends decides what it can shadow.
# A Node script cannot shadow Node3D members, so checking every class against
# every name would invent warnings Godot never prints.
BASE_CHAIN = {
    "Node": ["Node", "Object"],
    "Node3D": ["Node3D", "Node", "Object"],
    "RigidBody3D": ["Node3D", "Node", "Object"],
    "CharacterBody3D": ["Node3D", "Node", "Object"],
    "Area3D": ["Node3D", "Node", "Object"],
    "RayCast3D": ["Node3D", "Node", "Object"],
    "MeshInstance3D": ["Node3D", "Node", "Object"],
    "Camera3D": ["Node3D", "Node", "Object"],
    "Control": ["CanvasItem", "Node", "Object"],
    "CanvasItem": ["CanvasItem", "Node", "Object"],
    "CanvasLayer": ["Node", "Object"],
    "Resource": ["RefCounted", "Object"],
    "RefCounted": ["RefCounted", "Object"],
}


def load_api():
    path = os.path.join(ROOT, "tools", "godot_api.json")
    return json.load(open(path, encoding="utf-8"))


def base_of(text):
    m = re.search(r"^extends\s+(\w+)", text, re.M)
    return m.group(1) if m else "Object"


def strip_strings_and_comments(text):
    """Remove block strings, quoted text and comments.

    The shader source in damage.gd is a triple-quoted GDScript string full of
    words like `albedo` and `reference`; without this the scan reports matches
    inside it. This is the same trap that made two earlier checks fiction.
    """
    # Replace each block string with as many blank lines as it occupied, so
    # reported line numbers still match the real file. Collapsing them to one
    # line reported damage.gd:385 for a problem on line 468 - a number that
    # sends you to the wrong place, which is worse than no number.
    def blank_out(match):
        return '""' + "\n" * match.group(0).count("\n")

    text = re.sub(r'"""[\s\S]*?"""', blank_out, text)
    text = re.sub(r"'''[\s\S]*?'''", blank_out, text)
    text = re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', text)
    text = re.sub(r"'(?:[^'\\\n]|\\.)*'", '""', text)
    text = re.sub(r"#[^\n]*", "", text)
    return text


def check_file(path, api, problems):
    raw = open(path, encoding="utf-8").read()
    text = strip_strings_and_comments(raw)
    rel = os.path.relpath(path, ROOT)
    lines = text.split("\n")

    # Members of every class this script inherits from.
    inherited = set()
    for cls in BASE_CHAIN.get(base_of(text), ["Object"]):
        inherited |= set(api["members"].get(cls, []))

    functions = set(re.findall(r"^\s*(?:static\s+)?func\s+(\w+)", text, re.M))

    for number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()

        # 1 + 2: declarations - class variables, locals, and parameters.
        declared = []
        # Matches class variables and locals alike. `line` is stripped, so a
        # local inside a function looks identical here - which is the point:
        # SHADOWED_VARIABLE fires for locals too, and matching only
        # column-zero declarations silently missed the coolant_fraction case.
        m = re.match(r"^(?:@export\S*\s+)?(?:static\s+)?var\s+(\w+)", line)
        if m:
            declared.append(m.group(1))
        m = re.match(r"^(?:static\s+)?func\s+\w+\s*\((.*?)\)", line)
        if m:
            for part in m.group(1).split(","):
                part = part.strip()
                if not part:
                    continue
                name = re.match(r"^(\w+)", part)
                if name:
                    declared.append(name.group(1))

        for name in declared:
            if name in inherited:
                problems.append(
                    "%s:%d: \"%s\" shadows a member of the base class"
                    % (rel, number, name))
            elif name in functions:
                problems.append(
                    "%s:%d: \"%s\" shadows a function in this script"
                    % (rel, number, name))

    # 3: private class variables that nothing ever touches.
    for m in re.finditer(r"^(?:static\s+)?var\s+(_\w+)", text, re.M):
        name = m.group(1)
        uses = len(re.findall(r"\b%s\b" % re.escape(name), text))
        if uses <= 1:
            number = text[:m.start()].count("\n") + 1
            problems.append(
                "%s:%d: private variable \"%s\" is declared but never used"
                % (rel, number, name))


def main():
    api = load_api()
    problems = []
    files = sorted(glob.glob(os.path.join(ROOT, "scripts", "*.gd")))
    for path in files:
        check_file(path, api, problems)

    print("GDScript warning check")
    print("  %d scripts, base-class members from %s"
          % (len(files), ", ".join(sorted(api["members"]))))

    if problems:
        for p in problems:
            print("  FAIL " + p)
        print("\n%d PROBLEM(S)" % len(problems))
        return 1

    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
