from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from typing import TextIO

import pexpect
from shasta.ast_node import (
    AndNode,
    ArgChar,
    ArithForNode,
    ArithNode,
    AstNode,
    BackgroundNode,
    CArgChar,
    CaseNode,
    CommandNode,
    CondNode,
    CoprocNode,
    DefunNode,
    DupRedirNode,
    FileRedirNode,
    ForNode,
    GroupNode,
    HeredocRedirNode,
    IfNode,
    NotNode,
    OrNode,
    PipeNode,
    QArgChar,
    RedirectionNode,
    RedirNode,
    SelectNode,
    SemiNode,
    SingleArgRedirNode,
    SubshellNode,
    TimeNode,
    WhileNode,
)

from sh_expand.expand import (
    ImpureExpansion,
    StuckExpansion,
    Unimplemented,
)

PS1 = "EXPECT$ "
RCFILE = os.path.join(os.path.dirname(__file__), "bashrc.sh")

BASH_COMMAND = [
    "/usr/bin/env",
    "bash",
    "--rcfile",
    RCFILE,
    "--noediting",
    "-i",
    "-a",  # exports all vars, needed for pash
]
STR_COMMAND = " ".join(BASH_COMMAND)


def expand_command(
    ast: AstNode,
    exp_state: BashExpansionState,
    env_vars_file: str,
    env_vars_state: dict,
) -> AstNode:
    exp_state.run_command(source_file_cmd(env_vars_file))
    check_dangerous_sets(env_vars_state)
    # TODO: Reflect shopts
    # I suspect most won't be relevant
    # given there are no globs or process substitutions
    return compile_node(ast, exp_state)


# NOTE: Shell expansion is very complex.
# I accounted for the cases I recognized, but it's likely that
# there are cases which I missed.

# NOTE: This is temporary code, until we have proper arg chars.
# It is janky.

# NOTE: This is doesn't check for cases where pash would error anyways
# like eval, set -u in the region, etc. This limits its usage without pash.

# Design:

# 1. Any word that could cause or be effected by a side effect
# (process substitution or globbing) raise an exception
# 2. Anything with a to-expand character is expanded with quotes
# 3. Anything else is left as is

# ? is to handle globbing but also ${x:?err msg}
globbing_chars = set("*?!")
dangerous_tildes = {"~+", "~-"}

# Quotes are expanded to normalize " vs ' vs $'
need_to_expand_chars = set("{$~[\"'")


def should_expand_var(word: list[CArgChar]) -> bool:
    expand = False
    chars = []
    seen_dollar_sign = False

    # arithmetic should be safe
    start = join_argchars_as_str(word[:3])
    if start == "$((" and not any(c.char == ord("=") for c in word):
        return True

    for i, carg in enumerate(word):
        char = chr(carg.char)
        chars.append(char)
        if char == "\x7f":
            # TODO: Probably some pexpect bug
            # script runs in bash
            raise StuckExpansion("Delete character", carg)
        if char in globbing_chars:
            raise Unimplemented("Potential globbing:", carg)
        if char == "`":
            raise ImpureExpansion("Potential backtick process substitution:", carg)
        if seen_dollar_sign and char == "=":
            raise ImpureExpansion("Potential assignment", carg)

        pair = "".join(chars[-2:])
        if pair in dangerous_tildes:
            raise ImpureExpansion("Potential dangerous tilde expansion:", carg)
        if pair in {"<(", ">(", "$("}:
            raise ImpureExpansion("Potential process substitution", carg)
        if char == "(":
            raise ImpureExpansion("Potential array", carg)
        if char == "$":
            seen_dollar_sign = True
        if char in need_to_expand_chars:
            expand = True

    return expand


def check_dangerous_sets(env_dict):
    unsafe_sets = "u"
    try:
        sets = env_dict["$-"]
    except KeyError:
        pass
    else:
        if any((unsafe_s := s) in sets for s in unsafe_sets):
            raise Unimplemented("can't handle the set:", unsafe_s)


def source_file_cmd(file: str) -> str:
    return f"source '{file}' 2> /dev/null"


def default_log(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


def str_to_quoted_arg_char(data: str) -> QArgChar:
    return QArgChar([CArgChar(ord(c)) for c in data])


def str_to_arg_chars(data: str) -> list[CArgChar]:
    return [CArgChar(ord(c)) for c in data]


def join_argchars_as_str(data: list[ArgChar]) -> str:
    return "".join(c.format() for c in data)


class BashExpansionState:
    temp_dir: str | None
    debug: bool

    bash_mirror: pexpect.spawn
    is_open: bool

    def __init__(
        self,
        *,
        temp_dir: str | None = None,
        debug: bool = False,
        logger: Callable[..., None] = default_log,
        open: bool = False,
    ):
        self.is_open = False
        self.temp_dir = temp_dir
        self.debug = debug
        self._log = logger
        if open:
            self.open()

    def spawn_bash(self) -> pexpect.spawn:
        p = pexpect.spawn(
            BASH_COMMAND[0],
            BASH_COMMAND[1:],
            encoding="utf-8",
            echo=False,
            timeout=1,
        )
        p.expect_exact(PS1)
        if self.debug:
            log_path, log_file = self.make_temp_file("bash_mirror_log")
            self.log("bash mirror log saved in:", log_path)
            p.logfile = log_file

        return p

    def open(self):
        if self.is_open:
            self.close()

        self.bash_mirror = self.spawn_bash()
        self.is_open = True

    def close(self):
        if not self.is_open:
            return

        self.is_open = False
        if self.bash_mirror.logfile is not None:
            self.bash_mirror.logfile.close()
        try:
            self.bash_mirror.close(force=True)
        except pexpect.ExceptionPexpect:
            # TODO: Fails sometimes
            pass

    @contextmanager
    def subshell(self):
        try:
            self.run_command(STR_COMMAND)
            yield
        finally:
            self.run_command("exit")

    def expand_word(self, word: str) -> list[str]:
        assert self.is_open
        self.log("To expand with bash:", word)

        # null seperated to avoid spliting on data
        command = rf"printf '%s\0' {word}"
        self.log(f"Command to run: {repr(command)}")
        output = self.run_command(command)

        # remove trailing \0
        split_output = output.split("\0")[:-1]
        self.log("Bash expansion output is:", split_output)
        return split_output

    def expand_no_split(self, word: str):
        assert self.is_open
        self.log("To expand with bash:", word)

        command = f"echo -n {word}"
        output = self.run_command(command)

        self.log("Bash expansion output is:", output)
        return output

    def run_command(self, bash_command: str) -> str:
        assert self.is_open

        self.log("Executing bash command in mirror:", bash_command)

        self.bash_mirror.sendline(bash_command)
        self.bash_mirror.expect_exact(PS1)

        data = self.bash_mirror.before
        self.log(f"Mirror done with output {data}")

        return data

    def log(self, *args, **kwargs):
        if self.debug:
            self._log(*args, **kwargs)

    def make_temp_file(self, prefix: str | None = None) -> tuple[str, TextIO]:
        fd, path = tempfile.mkstemp(dir=self.temp_dir, prefix=prefix, text=True)
        file = os.fdopen(fd, "w+")
        return path, file


# ---------------------


def compile_node(ast_object: AstNode, exp_state: BashExpansionState) -> AstNode:
    node_name = ast_object.NodeName

    if node_name == "Pipe":
        return compile_node_pipe(ast_object, exp_state)
    elif node_name == "Command":
        return compile_node_command(ast_object, exp_state)
    elif node_name == "Subshell":
        return compile_node_subshell(ast_object, exp_state)
    elif node_name == "And":
        return compile_node_and(ast_object, exp_state)
    elif node_name == "Or":
        return compile_node_or(ast_object, exp_state)
    elif node_name == "Semi":
        return compile_node_semi(ast_object, exp_state)
    elif node_name == "Not":
        return compile_node_not(ast_object, exp_state)
    elif node_name == "Redir":
        return compile_node_redir(ast_object, exp_state)
    elif node_name == "Background":
        return compile_node_background(ast_object, exp_state)
    elif node_name == "Defun":
        return compile_node_defun(ast_object, exp_state)
    elif node_name == "For":
        return compile_node_for(ast_object, exp_state)
    elif node_name == "While":
        return compile_node_while(ast_object, exp_state)
    elif node_name == "If":
        return compile_node_if(ast_object, exp_state)
    elif node_name == "Case":
        return compile_node_case(ast_object, exp_state)
    elif node_name == "Select":
        return compile_node_select(ast_object, exp_state)
    elif node_name == "Arith":
        return compile_node_arith(ast_object, exp_state)
    elif node_name == "Cond":
        return compile_node_cond(ast_object, exp_state)
    elif node_name == "ArithFor":
        return compile_node_arith_for(ast_object, exp_state)
    elif node_name == "Coproc":
        return compile_node_coproc(ast_object, exp_state)
    elif node_name == "Time":
        return compile_node_time(ast_object, exp_state)
    elif node_name == "Group":
        return compile_node_group(ast_object, exp_state)
    else:
        raise NotImplementedError(f"Unknown node: {node_name}")


def compile_node_pipe(ast_node: PipeNode, exp_state: BashExpansionState):
    # TODO: Handle shopt -s lastpipe
    with exp_state.subshell():
        ast_node.items = [compile_node(item, exp_state) for item in ast_node.items]
    return ast_node


def compile_node_command(ast_node: CommandNode, exp_state: BashExpansionState):
    if ast_node.assignments:
        raise ImpureExpansion("Assignment", ast_node)

    ast_node.arguments = compile_command_arguments(ast_node.arguments, exp_state)

    # TODO: Allow declare, set, readonly, local, etc and remove the
    # corrosponding ast_node

    ast_node.redir_list = compile_redirections(ast_node.redir_list, exp_state)
    return ast_node


def compile_node_subshell(ast_node: SubshellNode, exp_state: BashExpansionState):
    with exp_state.subshell():
        ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


def compile_node_and(ast_node: AndNode, exp_state: BashExpansionState):
    ast_node.left_operand = compile_node(ast_node.left_operand, exp_state)
    ast_node.right_operand = compile_node(ast_node.right_operand, exp_state)
    return ast_node


def compile_node_or(ast_node: OrNode, exp_state: BashExpansionState):
    ast_node.left_operand = compile_node(ast_node.left_operand, exp_state)
    ast_node.right_operand = compile_node(ast_node.right_operand, exp_state)
    return ast_node


def compile_node_semi(ast_node: SemiNode, exp_state: BashExpansionState):
    ast_node.left_operand = compile_node(ast_node.left_operand, exp_state)
    ast_node.right_operand = compile_node(ast_node.right_operand, exp_state)
    return ast_node


def compile_node_not(ast_node: NotNode, exp_state: BashExpansionState):
    ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


def compile_node_redir(ast_node: RedirNode, exp_state: BashExpansionState):
    ast_node.node = compile_node(ast_node.node, exp_state)
    ast_node.redir_list = compile_redirections(ast_node.redir_list, exp_state)
    return ast_node


def compile_node_background(ast_node: BackgroundNode, exp_state: BashExpansionState):
    ast_node.redir_list = compile_redirections(ast_node.redir_list, exp_state)
    with exp_state.subshell():
        ast_node.node = compile_node(ast_node.node, exp_state)
    return ast_node


# NOTE: Any node that introduces a new variable
# cannot be expanded currently, since we cannot
# distinguish between the unexpandable new variable
# and the old variables


def compile_node_defun(ast_node: DefunNode, exp_state: BashExpansionState):
    raise Unimplemented("Invalidating the positional variables", ast_node)
    # ast_node.name = compile_command_argument(ast_node.name, exp_state)
    # ast_node.body = compile_node(ast_node.body, exp_state)
    # return ast_node


def compile_node_for(ast_node: ForNode, exp_state: BashExpansionState):
    raise Unimplemented("Invalidating the for loop variable", ast_node)
    # ast_node.variable = compile_command_argument(ast_node.variable, exp_state)
    # ast_node.argument = compile_command_arguments(ast_node.argument, exp_state)
    # # NOTE: The body might or might not be compiled depending on design
    # # ast_node.body = compile_node(ast_node.body, exp_state)
    # return ast_node


def compile_node_while(ast_node: WhileNode, exp_state: BashExpansionState):
    ast_node.test = compile_node(ast_node.test, exp_state)
    ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


# NOTE: In pash, a node like:

# > x=2
# > if command; do
# >     x=3
# > fi
# > {run command in parallel with $x}

# is impossible, so control flow should be safe.


def compile_node_if(ast_node: IfNode, exp_state: BashExpansionState):
    ast_node.cond = compile_node(ast_node.cond, exp_state)
    ast_node.then_b = compile_node(ast_node.then_b, exp_state)
    ast_node.else_b = (
        compile_node(ast_node.else_b, exp_state) if ast_node.else_b else None
    )
    return ast_node


def compile_node_case(ast_node: CaseNode, exp_state: BashExpansionState):
    ast_node.argument = compile_command_argument(ast_node.argument, exp_state)
    ast_node.cases = compile_command_cases(ast_node.cases, exp_state)
    return ast_node


def compile_node_select(ast_node: SelectNode, exp_state: BashExpansionState):
    raise Unimplemented("Invalidating the select variable", ast_node)
    # ast_node.variable = compile_command_argument(ast_node.variable, exp_state)
    # ast_node.body = compile_node(ast_node.body, exp_state)
    # ast_node.map_list = compile_command_arguments(ast_node.map_list, exp_state)
    # return ast_node


def compile_node_arith(ast_node: ArithNode, exp_state: BashExpansionState):
    ast_node.body = compile_arith_arguments(ast_node.body, exp_state)
    return ast_node


def compile_node_cond(ast_node: CondNode, exp_state: BashExpansionState):
    ast_node.op = (
        compile_command_argument(ast_node.op, exp_state) if ast_node.op else None
    )
    ast_node.left = (
        compile_command_argument(ast_node.left, exp_state) if ast_node.left else None
    )
    ast_node.right = (
        compile_command_argument(ast_node.right, exp_state) if ast_node.right else None
    )
    return ast_node


def compile_node_arith_for(ast_node: ArithForNode, exp_state: BashExpansionState):
    raise Unimplemented("Invalidating the for loop variable", ast_node)
    # ast_node.init = compile_command_arguments(ast_node.init, exp_state)
    # ast_node.cond = compile_command_arguments(ast_node.cond, exp_state)
    # ast_node.step = compile_command_arguments(ast_node.step, exp_state)
    # ast_node.action = compile_node(ast_node.action, exp_state)
    # return ast_node


def compile_node_coproc(ast_node: CoprocNode, exp_state: BashExpansionState):
    raise Unimplemented("Invalidating the coproc variable", ast_node)
    # ast_node.name = compile_command_argument(ast_node.name, exp_state)
    # ast_node.body = compile_node(ast_node.body, exp_state)
    # return ast_node


def compile_node_time(ast_node: TimeNode, exp_state: BashExpansionState):
    ast_node.command = compile_node(ast_node.command, exp_state)
    return ast_node


def compile_node_group(ast_node: GroupNode, exp_state: BashExpansionState):
    ast_node.body = compile_node(ast_node.body, exp_state)
    # not supported currently
    # ast_node.redirections = compile_redirections(ast_node.redirections, exp_state)
    return ast_node


def compile_command_arguments(
    arguments: list[list[CArgChar]], exp_state: BashExpansionState
) -> list[list[QArgChar] | list[CArgChar]]:
    args: list[list[QArgChar] | list[CArgChar]] = []
    for argument in arguments:
        compiled_arg = compile_command_argument(argument, exp_state)
        args.extend(compiled_arg)
    return args


def compile_command_argument(
    argument: list[CArgChar], exp_state: BashExpansionState, split=True
) -> list[list[QArgChar]] | list[list[CArgChar]]:
    str_arg = join_argchars_as_str(argument)
    exp_result = should_expand_var(argument)
    if exp_result and split:
        expanded = exp_state.expand_word(str_arg)
    elif exp_result and not split:
        expanded = [exp_state.expand_no_split(str_arg)]
    else:
        return [argument]
    nodes = [[str_to_quoted_arg_char(ea)] for ea in expanded]
    return nodes


def compile_arith_arguments(
    arguments: list[list[CArgChar]], exp_state: BashExpansionState
) -> list[CArgChar]:
    args = []
    for argument in arguments:
        argument = compile_arith_argument(argument, exp_state)
        args.append(argument)
    return args


def compile_arith_argument(
    argument: list[CArgChar], exp_state: BashExpansionState
) -> list[CArgChar]:
    expanded = exp_state.expand_no_split(join_argchars_as_str(argument))
    nodes = [str_to_arg_chars(ea) for ea in expanded]
    return nodes


def compile_redirections(
    redir_list: list[RedirectionNode], exp_state: BashExpansionState
) -> list[RedirectionNode]:
    return [compile_redirection(redir, exp_state) for redir in redir_list]


def compile_redirection(
    redir: RedirectionNode, exp_state: BashExpansionState
) -> RedirectionNode:
    type = redir.NodeName
    if type == "File":
        return compile_redirection_file(redir, exp_state)
    elif type == "Dup":
        return compile_redirection_dup(redir, exp_state)
    elif type == "Heredoc":
        return compile_redirection_here(redir, exp_state)
    elif type == "SingleArg":
        return compile_redirection_single_arg(redir, exp_state)
    else:
        raise NotImplementedError(f"Unknown redirection type: {type}")


def compile_command_cases(
    cases: list[dict], exp_state: BashExpansionState
) -> list[dict]:
    return [compile_command_case(case, exp_state) for case in cases]


def compile_command_case(case: dict, exp_state: BashExpansionState) -> dict:
    case["pattern"] = compile_command_argument(case["pattern"], exp_state, split=False)[
        0
    ]
    case["body"] = compile_node(case["body"], exp_state)
    return case


def compile_redirection_file(
    redir: FileRedirNode, exp_state: BashExpansionState
) -> FileRedirNode:
    if redir.fd[0] == "var":
        raise ImpureExpansion("Runtime fd:", redir)
    redir.arg = compile_command_argument(redir.arg, exp_state, split=False)[0]
    return redir


def compile_redirection_dup(
    redir: DupRedirNode, exp_state: BashExpansionState
) -> DupRedirNode:
    if redir.fd[0] == "var":
        raise ImpureExpansion("Runtime fd:", redir)
    if redir.arg[0] == "var":
        raise ImpureExpansion("Runtime fd:", redir)
    return redir


def compile_redirection_here(
    redir: HeredocRedirNode, exp_state: BashExpansionState
) -> HeredocRedirNode:
    if redir.fd[0] == "var":
        raise ImpureExpansion("Runtime fd:", redir)
    redir.arg = compile_command_argument(redir.arg, exp_state, split=False)[0]
    return redir


def compile_redirection_single_arg(
    redir: SingleArgRedirNode, exp_state: BashExpansionState
) -> SingleArgRedirNode:
    if redir.fd[0] == "var":
        raise ImpureExpansion("Runtime fd:", redir)
    return redir
