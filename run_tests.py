import copy
import logging
import os
import traceback

import libdash.parser
from shasta.ast_node import *
from shasta.json_to_ast import to_ast_node

from sh_expand import expand, env_vars_util

TEST_PATH = "./tests/expansion"

variables = env_vars_util.read_vars_file(os.path.join(TEST_PATH, "sample.env"))
# logging.info(variables)

test_success = True

# logger = logging.getLogger()
# logger.setLevel(logging.DEBUG)

## Keeps track of the first time we call the parser
first_time_calling_parser = True

## Parses straight a shell script to an AST
## through python without calling it as an executable
def parse_shell_to_asts(input_script_path) -> "list[AstNode]":
    global first_time_calling_parser

    try:
        ## The libdash parser must not be initialized when called the second
        ## time because it hangs!
        new_ast_objects = libdash.parser.parse(input_script_path, init=first_time_calling_parser)
        first_time_calling_parser = False

        ## Transform the untyped ast objects to typed ones
        typed_ast_objects = []
        for untyped_ast, _original_text, _linno_before, _linno_after, in new_ast_objects:
             typed_ast = to_ast_node(untyped_ast)
             typed_ast_objects.append(typed_ast)

        return typed_ast_objects
    except libdash.parser.ParsingException as e:
        logging.error(f'Parsing error: {e}')
        exit(1)


print("Parsing tests from {}".format(TEST_PATH))

tests = os.listdir(TEST_PATH)
tests = [test for test in tests if test.endswith(".sh")]
tests.sort()

print("* Analysis tests ")

def safety(b):
    if b:
        return "safe"
    else:
        return "unsafe"

analysis_failures = set()
for test_name in tests:
    test = os.path.join(TEST_PATH, test_name)
    ast_objects = parse_shell_to_asts(test)
    logging.info(f'test_name: {test_name}, Ast: {ast_objects}')

    expected_safe = test_name.startswith("safe")
    for (i, ast_object) in enumerate(ast_objects):
        is_safe = expand.safe_command(ast_object)
        
        if is_safe != expected_safe:
            print("{} command #{} expected {} got {}".format(test_name, i, expected_safe, is_safe))
            analysis_failures.add(test_name)

if len(analysis_failures) == 0:
    print("All {} tests passed".format(len(tests)))
else:
    test_success = False
    print("{}/{} tests failed: {}".format(len(analysis_failures), len(tests), analysis_failures))

print("\n* Expansion tests")

expansion_failures = set()
for test_name in tests:
    test = os.path.join(TEST_PATH, test_name)
    ast_objects = parse_shell_to_asts(test)

    expanded = os.path.join(TEST_PATH, test_name.replace("sh","expanded"))
    expected_safe = os.path.exists(expanded)
    exp_state = expand.ExpansionState(variables)
    for (i, ast_object) in enumerate(ast_objects):
        try:
            cmd = expand.expand_command(ast_object, copy.deepcopy(exp_state))
            got = cmd.pretty()

            # ??? MMG 2020-12-17 unsure about fixing the pretty printing (which may need these backslashes!)
            got = got.replace("\\'", "'")

            if not expected_safe:
                print("Unexpected success in", test_name)
                print(got)
                expansion_failures.add(test_name)
            else:
                expected = open(expanded).read()

                if got != expected:
                    print(f"In {test_name}, expected:\n\t",expected,"Got:\n\t",got)
                    expansion_failures.add(test_name)
        except (expand.EarlyError, expand.StuckExpansion, expand.ImpureExpansion, expand.Unimplemented) as e:
            if expected_safe:
                print("Found unexpected failure in", test_name)
                print("Error:", traceback.format_exc())
                expansion_failures.add(test_name)
            else:
                print("Found expected failure in", test_name)
        except Exception as e:
            print("Error:", traceback.format_exc())
            expansion_failures.add(test_name)

if len(expansion_failures) == 0:
    print("All {} tests passed".format(len(tests)))
else:
    print("{}/{} tests failed: {}".format(len(expansion_failures), len(tests), expansion_failures))
    test_success = False

if test_success:
    exit(0)
else:
    exit(1)
