from __future__ import annotations

import os
import re
import sys
import tempfile
from collections.abc import Callable
from typing import TextIO

import pexpect
from shasta.ast_node import (
    AndNode,
    ArithForNode,
    ArithNode,
    AssignNode,
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

PS1 = "EXPECT$ "
ESCAPED_PS1 = re.escape(PS1)


def expand_command(
    ast: AstNode, exp_state: BashExpansionState, env_vars_file: str | None = None
) -> AstNode:
    if env_vars_file is not None:
        exp_state.sync_run_line_command_mirror(f"source '{env_vars_file}'")
    return compile_node(ast, exp_state)


def should_expand(word: list[CArgChar]) -> bool:
    # somewhat conservative
    special_chars = {ord(c) for c in ("$", "`", "~")}
    return any(c.char in special_chars for c in word)


def default_log(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


class BashExpansionState:
    temp_dir: str | None
    var_file: TextIO
    var_file_path: str

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

    def open(self):
        if self.is_open:
            return
        var_file_fd, var_file_path = tempfile.mkstemp(
            dir=self.temp_dir, prefix="bash_expand_vars", text=True
        )
        self.var_file_path = var_file_path
        self.var_file = os.fdopen(var_file_fd, "w+")

        ## Spawn a bash process to ask it for expansions
        p = pexpect.spawn(
            "/usr/bin/env",
            ["bash", "--norc", "--noediting", "-i"],
            encoding="utf-8",
            echo=False,
        )
        ## If we are in debug mode also log the bash's output
        if self.debug:
            log_path, log_file = self.make_temp_file("bash_mirror_log")
            self.log("bash mirror log saved in:", log_path)
            p.logfile = log_file

        self.bash_mirror = p
        self.is_open = True
        self.set_ps1()


    def close(self):
        self.is_open = False
        self.bash_mirror.logfile.close()
        self.bash_mirror.close(force=True)
        self.var_file.close()
        os.remove(self.var_file_path)

    def set_ps1(self):
        # must be read only to prevent the expansion from being messed up
        self.sync_run_line_command_mirror(f"readonly PS1='{PS1}'")

    def update_var_file(self, assignment: str):
        assert self.is_open
        self.var_file.write(assignment + "\n")

    def update_bash_mirror_vars(self):
        assert self.is_open

        self.sync_run_line_command_mirror(f"source '{self.var_file_path}'")

        # variables have been saved in the bash process
        self.var_file.truncate(0)

    def expand_word(self, word: str) -> list[str]:
        assert self.is_open

        self.log("To expand with bash:", word)

        # null seperated to avoid spliting on data
        command = rf"printf '%s\0' {word}"
        output = self.sync_run_line_command_mirror(command)

        # remove trailing \0
        split_output = output.split("\0")[:-1]
        self.log("Bash expansion output is:", split_output)
        return split_output

    def sync_run_line_command_mirror(self, command: str) -> str:
        assert self.is_open

        bash_command = command
        self.log("Executing bash command in mirror:", bash_command)

        # Note: this will eventually need to be changed to support non-utf8 characters
        self.bash_mirror.sendline(str(bash_command))

        data = self.wait_bash_mirror()
        self.log("mirror done!")

        return data

    def wait_bash_mirror(self) -> str:
        assert self.is_open

        r = self.bash_mirror.expect(ESCAPED_PS1)
        assert r == 0
        output: str = self.bash_mirror.before

        ## I am not sure why, but \r s are added before \n s
        output = output.replace("\r\n", "\n")

        self.log("Before the prompt!")
        self.log(output.replace("\0", "<NUL>"))
        return output

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
    ast_node.items = [compile_node(item, exp_state) for item in ast_node.items]
    return ast_node


def compile_node_command(ast_node: CommandNode, exp_state: BashExpansionState):
    ast_node.assignments = compile_command_assignments(ast_node.assignments, exp_state)
    ast_node.arguments = compile_command_arguments(ast_node.arguments, exp_state)
    ast_node.redir_list = compile_redirections(ast_node.redir_list, exp_state)
    return ast_node


def compile_node_subshell(ast_node: SubshellNode, exp_state: BashExpansionState):
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
    ast_node.node = compile_node(ast_node.node, exp_state)
    ast_node.redir_list = compile_redirections(ast_node.redir_list, exp_state)
    return ast_node


def compile_node_defun(ast_node: DefunNode, exp_state: BashExpansionState):
    ast_node.name = compile_command_argument(ast_node.name, exp_state)
    ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


def compile_node_for(ast_node: ForNode, exp_state: BashExpansionState):
    ast_node.variable = compile_command_argument(ast_node.variable, exp_state)
    ast_node.argument = compile_command_arguments(ast_node.argument, exp_state)
    # NOTE: The body might or might not be compiled depending on design
    # ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


def compile_node_while(ast_node: WhileNode, exp_state: BashExpansionState):
    ast_node.test = compile_command_argument(ast_node.test, exp_state)
    ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


def compile_node_if(ast_node: IfNode, exp_state: BashExpansionState):
    ast_node.cond = compile_command_argument(ast_node.cond, exp_state)
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
    ast_node.variable = compile_command_argument(ast_node.variable, exp_state)
    ast_node.body = compile_node(ast_node.body, exp_state)
    ast_node.map_list = compile_command_arguments(ast_node.map_list, exp_state)
    return ast_node


def compile_node_arith(ast_node: ArithNode, exp_state: BashExpansionState):
    ast_node.body = compile_command_arguments(ast_node.body, exp_state)
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
    ast_node.init = compile_command_arguments(ast_node.init, exp_state)
    ast_node.cond = compile_command_arguments(ast_node.cond, exp_state)
    ast_node.step = compile_command_arguments(ast_node.step, exp_state)
    ast_node.action = compile_node(ast_node.action, exp_state)
    return ast_node


def compile_node_coproc(ast_node: CoprocNode, exp_state: BashExpansionState):
    ast_node.name = compile_command_argument(ast_node.name, exp_state)
    ast_node.body = compile_node(ast_node.body, exp_state)
    return ast_node


def compile_node_time(ast_node: TimeNode, exp_state: BashExpansionState):
    ast_node.command = compile_node(ast_node.command, exp_state)
    return ast_node


def compile_node_group(ast_node: GroupNode, exp_state: BashExpansionState):
    ast_node.body = compile_node(ast_node.body, exp_state)
    ast_node.redirections = compile_redirections(ast_node.redirections, exp_state)
    return ast_node


def compile_command_arguments(
    arguments: list[list[CArgChar]], exp_state: BashExpansionState
) -> list[list[QArgChar] | list[CArgChar]]:
    args: list[list[QArgChar] | list[CArgChar]] = []
    for argument in arguments:
        compiled_arg = compile_command_argument(argument, exp_state)
        if should_expand(argument):
            # flatten
            args.extend(compiled_arg)
        else:
            args.append(compiled_arg)
    return args


# can word expand, so either returns a list of quoted expanded args
# or the original argument
def compile_command_argument(
    argument: list[CArgChar], exp_state: BashExpansionState
) -> list[CArgChar] | list[list[QArgChar]]:
    if not should_expand(argument):
        return argument
    expanded = exp_state.expand_word("".join(chr(c.char) for c in argument))
    nodes = [[str_to_quoted_arg_char(ea)] for ea in expanded]
    return nodes


def str_to_quoted_arg_char(data: str) -> QArgChar:
    return QArgChar([CArgChar(ord(c)) for c in data])


def compile_command_assignments(
    assignments: list[AssignNode], exp_state: BashExpansionState
) -> list[AssignNode]:
    return [
        compile_command_assignment(assignment, exp_state) for assignment in assignments
    ]


def compile_command_assignment(
    assignment: AssignNode, exp_state: BashExpansionState
) -> AssignNode:
    assignment.val = compile_command_argument(assignment.val, exp_state)
    exp_state.update_var_file(assignment.pretty())
    return assignment


def compile_redirections(
    redir_list: list[RedirectionNode], exp_state: BashExpansionState
) -> list[RedirectionNode]:
    return [compile_redirection(redir, exp_state) for redir in redir_list]


def compile_redirection(
    redir: RedirectionNode, exp_state: BashExpansionState
) -> RedirectionNode:
    type = redir.redir_type
    if type == "File":
        return compile_redirection_file(redir, exp_state)
    elif type == "Dup":
        return compile_redirection_dup(redir, exp_state)
    elif type == "Here":
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
    case["pattern"] = compile_command_argument(case["pattern"], exp_state)
    case["body"] = compile_node(case["body"], exp_state)
    return case


def compile_redirection_file(
    redir: FileRedirNode, exp_state: BashExpansionState
) -> FileRedirNode:
    if redir.fd[0] == "var":
        redir.fd[1] = compile_command_argument(redir.fd[1], exp_state)
    redir.arg = compile_command_argument(redir.arg, exp_state)
    return redir


def compile_redirection_dup(
    redir: DupRedirNode, exp_state: BashExpansionState
) -> DupRedirNode:
    if redir.fd[0] == "var":
        redir.fd[1] = compile_command_argument(redir.fd[1], exp_state)
    if redir.arg[0] == "var":
        redir.arg[1] = compile_command_argument(redir.arg[1], exp_state)
    return redir


def compile_redirection_here(
    redir: HeredocRedirNode, exp_state: BashExpansionState
) -> HeredocRedirNode:
    if redir.fd[0] == "var":
        redir.fd[1] = compile_command_argument(redir.fd[1], exp_state)
    redir.arg = compile_command_argument(redir.arg, exp_state)
    return redir


def compile_redirection_single_arg(
    redir: SingleArgRedirNode, exp_state: BashExpansionState
) -> SingleArgRedirNode:
    if redir.fd[0] == "var":
        redir.fd[1] = compile_command_argument(redir.fd[1], exp_state)
    return redir
