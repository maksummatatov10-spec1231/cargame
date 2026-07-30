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
    """{path: {function name: [(param name, type, has_default), ...]}}

    Signatures are kept per file. GDScript methods live on their class, so two
    scripts can each define a private helper with the same name and different
    parameters - which they do. Treating the names as global produced a false
    "wrong number of arguments" report.
    """
    sigs = {}
    for path, text in sources.items():
        sigs[path] = {}
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
            sigs[path][name] = params
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


def check_calls(sources, all_sigs):
    for path, text in sources.items():
        sigs = all_sigs.get(path, {})
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


# Properties and methods on common Godot base classes that a local variable or
# parameter must not shadow. Godot reports these as SHADOWED_VARIABLE_BASE_CLASS
# and they are easy to introduce by accident.
BASE_CLASS_MEMBERS = {
    "Node": ["name", "owner", "scene_file_path", "process_mode", "multiplayer"],
    "Node3D": ["basis", "scale", "position", "rotation", "transform", "quaternion",
               "global_position", "global_rotation", "global_transform", "visible",
               "top_level"],
    "CanvasItem": ["material", "modulate", "visible", "z_index"],
    "Control": ["size", "anchor_left", "theme", "tooltip_text"],
    "RigidBody3D": ["mass", "inertia", "linear_velocity", "angular_velocity",
                    "gravity_scale", "center_of_mass", "freeze"],
    "RayCast3D": ["enabled", "target_position", "collision_mask", "exclude_parent"],
}

# Global functions that a variable must not shadow (SHADOWED_GLOBAL_IDENTIFIER).
GLOBAL_FUNCTIONS = {
    "load", "preload", "print", "range", "min", "max", "abs", "sign", "clamp",
    "lerp", "str", "int", "float", "bool", "type_of", "instance_from_id",
    "hash", "len", "assert", "char", "ord", "round", "floor", "ceil", "pow",
    "sqrt", "sin", "cos", "tan", "log", "exp", "randi", "randf", "seed",
}


def check_unguarded_indexing(sources):
    """Flags [0] / [1] indexing that is not guarded anywhere in its function.

    This is the class of bug behind "Out of bounds get index '0'": a child node
    readying before its parent saw an empty array and indexed straight into it.
    The whole enclosing function is searched for a guard, because the check and
    the access are often far apart.
    """
    for path, text in sources.items():
        lines = text.splitlines()

        # Map each line to the body of the function it belongs to.
        starts = [i for i, l in enumerate(lines)
                  if re.match(r"\s*(?:static\s+)?func\s+\w+", l)]
        starts.append(len(lines))

        for fi in range(len(starts) - 1):
            body = lines[starts[fi]:starts[fi + 1]]
            joined = "\n".join(l.split("#")[0] for l in body)
            for offset, raw in enumerate(body):
                line = raw.split("#")[0]
                for m in re.finditer(r"\b(_?\w+)\[([0-9])\]", line):
                    name, idx = m.group(1), m.group(2)
                    if name in ("arrays", "params", "argv"):
                        continue
                    guarded = (
                        ("%s.is_empty()" % name) in joined
                        or ("%s.size()" % name) in joined
                        or ("not %s" % name) in joined
                        or ("in %s" % name) in joined
                        or ("%s ==" % name) in joined
                        or re.search(r"%s\s*=\s*\[" % re.escape(name), joined)
                    )
                    if not guarded:
                        PROBLEMS.append(
                            "%s:%d: %s[%s] is not guarded against an empty array"
                            % (os.path.basename(path),
                               starts[fi] + offset + 1, name, idx))


def check_shadowing(sources):
    """Finds locals and parameters that shadow a base class member or a global."""
    for path, text in sources.items():
        base = None
        m = re.search(r"^extends\s+(\w+)", text, re.M)
        if m:
            base = m.group(1)
        # Walk up the small hierarchy we care about.
        chain = []
        seen_base = base
        order = ["RayCast3D", "RigidBody3D", "Control", "CanvasItem", "Node3D", "Node"]
        for cls in order:
            if seen_base == cls or (seen_base and cls in ("Node3D", "Node")):
                chain.append(cls)
        if seen_base in BASE_CLASS_MEMBERS and seen_base not in chain:
            chain.append(seen_base)

        forbidden = set()
        for cls in chain:
            forbidden.update(BASE_CLASS_MEMBERS.get(cls, []))

        lines = text.splitlines()
        for i, raw in enumerate(lines, 1):
            line = raw.split("#")[0]

            # local variable declarations
            for m in re.finditer(r"\bvar\s+(\w+)\s*[:=]", line):
                nm = m.group(1)
                if not line.lstrip().startswith("var"):
                    continue          # a class-level var is allowed to be named freely
                if nm in forbidden:
                    PROBLEMS.append("%s:%d: local variable \"%s\" shadows %s.%s"
                                    % (os.path.basename(path), i, nm, base, nm))
                if nm in GLOBAL_FUNCTIONS:
                    PROBLEMS.append("%s:%d: variable \"%s\" shadows the built-in "
                                    "function %s()"
                                    % (os.path.basename(path), i, nm, nm))

            # function parameters
            fm = re.match(r"\s*(?:static\s+)?func\s+\w+\s*\((.*)", line)
            if fm:
                params = fm.group(1).split(")")[0]
                for p in split_args(params):
                    nm = p.split(":")[0].split("=")[0].strip()
                    if not nm:
                        continue
                    if nm in forbidden:
                        PROBLEMS.append("%s:%d: parameter \"%s\" shadows %s.%s"
                                        % (os.path.basename(path), i, nm, base, nm))
                    if nm in GLOBAL_FUNCTIONS:
                        PROBLEMS.append("%s:%d: parameter \"%s\" shadows the "
                                        "built-in function %s()"
                                        % (os.path.basename(path), i, nm, nm))


# Node paths used with $ or get_node() that must exist in the scene the script
# is attached to. Checked against the generated scenes.
# Which scene each script is attached to as the *root* node. A bare $Child on
# these scripts is resolved from that root, so the path must exist there.
ROOT_SCRIPTS = {
    "vehicle.gd": ["car.tscn", "pickup.tscn"],
    "game.gd": ["main.tscn"],
}


def _scene_children(text):
    """{full path from the scene root: True} for one .tscn."""
    out = set()
    for m in re.finditer(r'\[node name="([^"]+)"(?:[^\]]*?parent="([^"]+)")?', text):
        child, parent = m.group(1), m.group(2)
        if parent is None:
            continue                      # the root node itself
        out.add(child if parent == "." else parent + "/" + child)
    return out


def check_node_paths(sources):
    """Flags $Path that will not resolve at runtime.

    'Node not found: "Model"' came from `@onready var _model := $Model` after
    the model was moved under a smoothing node. `Model` still existed in the
    scene, just at `Smooth/Model`, so merely checking that the *name* appears
    somewhere is not enough - the path has to be resolvable from the node the
    script is actually on.
    """
    scene_dir = os.path.join(ROOT, "scenes")
    if not os.path.isdir(scene_dir):
        return
    scenes = {}
    for f in os.listdir(scene_dir):
        if f.endswith(".tscn"):
            scenes[f] = _scene_children(open(os.path.join(scene_dir, f)).read())

    # Paths reachable from anywhere, for scripts we cannot attribute to a scene.
    anywhere = set()
    for paths in scenes.values():
        for p in paths:
            parts = p.split("/")
            for i in range(len(parts)):
                anywhere.add("/".join(parts[i:]))

    for path, text in sources.items():
        base = os.path.basename(path)
        owners = ROOT_SCRIPTS.get(base)
        # Comments and strings mention node paths in prose; only code counts.
        code = "\n".join(re.sub(r'"[^"]*"', '""', l).split("#")[0]
                         for l in text.splitlines())
        for m in re.finditer(r'(?<![\w.])\$([A-Za-z_][\w/]*)', code):
            target = m.group(1)
            line = code[:m.start()].count("\n") + 1
            if owners:
                # Must resolve from the root of every scene using this script.
                missing = [s for s in owners
                           if s in scenes and target not in scenes[s]]
                if missing:
                    PROBLEMS.append(
                        "%s:%d: $%s is not a child of the root in %s"
                        % (base, line, target, ", ".join(missing)))
            elif target not in anywhere:
                PROBLEMS.append(
                    "%s:%d: $%s does not exist in any scene" % (base, line, target))


def main():
    scripts = os.path.join(ROOT, "scripts")
    sources = {}
    for name in sorted(os.listdir(scripts)):
        if name.endswith(".gd"):
            sources[os.path.join(scripts, name)] = open(os.path.join(scripts, name)).read()

    sigs = collect_signatures(sources)
    total = sum(len(v) for v in sigs.values())
    print("GDScript call type check")
    print("  %d scripts, %d typed functions" % (len(sources), total))
    check_calls(sources, sigs)
    check_shadowing(sources)
    check_unguarded_indexing(sources)
    check_node_paths(sources)

    if PROBLEMS:
        print("\n%d problem(s) found:" % len(PROBLEMS))
        for p in PROBLEMS:
            print("  " + p)
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
