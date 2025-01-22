# sh-expand

A python library for expanding shell ASTs that can be used to construct sound analyses for shell scripts (after having expanded the AST).

## Design

There are two implementations of shell expansion:
1. `expand.py` does an in-python expansion of simple & safe AST nodes. It doesn't support bashisms.
2. `bash_expand.py` expands by conservatively echoing safe commands to a bash process containing the current shell state.

Both, when called via `expand_command`, recurse down into the words of a shasta AST node to
expand, leave the word as is, or raise an ExpansionError. Examples:
- Process substitutions are unsafe.
- Simple variable substitutions are safe.
- Assignments are considered unsafe, since in Pash they indicate an unparallelizable region.

`expand.py` interprets the ArgChars libdash parses (ex: for variable and process substitutions)
to check and expand words.

Libbash does not parse ArgChars, so `bash_expand.py` instead conservatively checks
for the presence of potentially expandable or unsafe literal characters (see `should_expand_var`)
before expanding.

Not having proper ArgChar parsing causes false positive unsafe errors,
so `expand.py` should eventually be extended to support bash-only AST nodes,
and libbash and shasta extended to parse ArgChars.
