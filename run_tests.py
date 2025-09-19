import argparse
import copy
import itertools
import json
import logging
import os
import traceback

import libdash.parser
from libbash import bash_to_ast
from shasta.ast_node import *
from shasta.json_to_ast import to_ast_node
from shasta.bash_to_shasta_ast import to_ast_node as bash_to_ast_node

from sh_expand import expand, env_vars_util, bash_expand

TEST_PATH = "./tests/"
TEST_EXPANSION_PATH = os.path.join(TEST_PATH, "expansion")
TEST_VAR_PARSE_PATH = os.path.join(TEST_PATH, "variable_parse")
MODES = ("dash", "bash")

## Keeps track of the first time we call the parser
first_time_calling_parser = True

## Parses straight a shell script to an AST
## through python without calling it as an executable
def parse_dash_to_asts(input_script_path) -> "list[AstNode]":
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



def parse_bash_to_asts(input_script_path) -> "list[AstNode]":
    try:
        new_bash_ast_objects = bash_to_ast(input_script_path) 

        bash_ast_objects = []

        for untyped_bash_ast in new_bash_ast_objects:
            bash_typed_ast = bash_to_ast_node(untyped_bash_ast)
            bash_ast_objects.append(bash_typed_ast)

        return bash_ast_objects
    except libdash.parser.ParsingException as e:
        logging.error(f'Parsing error: {e}')
        exit(1)


def parse_args():
    parser = argparse.ArgumentParser()
    ## TODO: Import the arguments so that they are not duplicated here and in orch
    parser.add_argument("-d", "--debug",
                        action="store_true",
                        help="Print debugging output")
    args, unknown_args = parser.parse_known_args()
    return args

def init(args):
    logger = logging.getLogger()
    if args.debug:
        logger.setLevel(logging.DEBUG)


def print_report(total: set, failures_set: set, skipped_set: set):
    skipped = len(skipped_set)
    failed = len(failures_set)
    valid_tests = len(total) - skipped

    if failed == 0:
        print("All non-skipped {} tests passed".format(valid_tests))
    else:
        test_success = False
        print("{}/{} tests failed: {}".format(failed, valid_tests, failures_set))

    if skipped > 0:
        print(" |- Skipped tests {}".format(skipped_set))


test_success = True

## Parse arguments and initialize
args = parse_args()
init(args)

variable_path = os.path.join(TEST_EXPANSION_PATH, "sample.env")
variables = env_vars_util.read_vars_file(variable_path, (5, 0, 17))
logging.info(variables)

print("Parsing tests from {}".format(TEST_EXPANSION_PATH))

expansion_tests = os.listdir(TEST_EXPANSION_PATH)
expansion_tests = [test for test in expansion_tests if test.endswith(".sh")]
expansion_tests.sort()

print("* Analysis tests (only for dash currently)")

analysis_failures = set()
analysis_skipped = set()
for test_name in expansion_tests:
    test = os.path.join(TEST_EXPANSION_PATH, test_name)
    logging.info(f'Test: {test_name}')

    skip_test = test_name.startswith("skip")
    if skip_test or "bash_only" in test_name:
        logging.info(f'Skipping...')
        analysis_skipped.add(test_name)
        continue

    ast_objects = parse_dash_to_asts(test)
    logging.info(f'Ast: {ast_objects}')

    expected_safe = test_name.startswith("safe")
    for (i, ast_object) in enumerate(ast_objects):
        is_safe = expand.safe_command(ast_object)
        
        if is_safe != expected_safe:
            print("{} command #{} expected {} got {}".format(test_name, i, expected_safe, is_safe))
            analysis_failures.add(test_name)

if len(analysis_failures) > 0:
    test_success = False

print_report(expansion_tests, analysis_failures, analysis_skipped)


print("\n* Expansion tests")

bash_expansion_tests = os.listdir(TEST_EXPANSION_PATH)
expansion_tests.extend(test for test in bash_expansion_tests if test.endswith(".sh"))

expansion_failures = set()
expansion_skipped = set()
dash_bash_expansion_tests = list(itertools.product(expansion_tests, MODES))
expansion_mode_tests = set()
for test_name, mode in dash_bash_expansion_tests:
    test = os.path.join(TEST_EXPANSION_PATH, test_name)
    logging.info(f'Test: {test_name}')

    skip_test = test_name.startswith("skip") or (mode == "dash" and "bash_only" in test_name)
    if skip_test:
        logging.info(f'Skipping...')
        expansion_skipped.add(test_name)
        continue

    if mode == "dash":
        ast_objects = parse_dash_to_asts(test)
    if mode == "bash":
        ast_objects = parse_bash_to_asts(test)
    logging.info(f' | {mode.title()} AST: {ast_objects}')

    expanded = os.path.join(TEST_EXPANSION_PATH, test_name.replace(".sh",".expanded"))
    expected_safe = os.path.exists(expanded)
    if mode == "dash":
        exp_state = expand.ExpansionState(variables)
        if "bash" in test_name:
            expected_safe = False
    if mode == "bash":
        exp_state = bash_expand.BashExpansionState(open=True)
    for (i, ast_object) in enumerate(ast_objects):
        try:
            mode_test_name = f"{test_name} with {mode}"
            expansion_mode_tests.add(mode_test_name)
            logging.info(f"{mode.title()} Expansion:")
            if mode == "dash":
                cmd = expand.expand_command(ast_object, copy.deepcopy(exp_state))
            elif mode == "bash":
                exp_state.open(["position1"])  # to create a new bash process
                cmd = bash_expand.expand_command(
                    ast_object,
                    exp_state,
                    variable_path,
                    variables
                )
            logging.info(f"Expanded cmd AST: {cmd}")
            got = cmd.pretty()
            logging.info(f"Expanded cmd: {got}")

            # ??? MMG 2020-12-17 unsure about fixing the pretty printing (which may need these backslashes!)
            got = got.replace("\\'", "'")
            got = got.rstrip()

            if not expected_safe:
                print("Unexpected success in", mode_test_name)
                print(got)
                expansion_failures.add(mode_test_name)
            else:
                expected = open(expanded).read()
                expected = expected.rstrip()

                if got != expected and got.replace("\"", "") != expected.replace("\"", ""):
                    print(f"In {mode_test_name}, expected:\n\t{expected}\nGot:\n\t{got}")
                    expansion_failures.add(mode_test_name)

        except (expand.EarlyError, expand.StuckExpansion, expand.ImpureExpansion, expand.Unimplemented) as e:
            if expected_safe:
                print("Found an unexpected failure in", mode_test_name)
                print("Error:", traceback.format_exc())
                expansion_failures.add(mode_test_name)
            else:
                print("Found an expected failure in", mode_test_name)
        except Exception as e:
            print(f"Error in {mode_test_name}:", traceback.format_exc())
            expansion_failures.add(mode_test_name)

if len(expansion_failures) > 0:
    test_success = False

print_report(expansion_mode_tests, expansion_failures, expansion_skipped)

print("\n* Variable parse tests")

var_parse_tests = os.listdir(TEST_VAR_PARSE_PATH)
var_parse_tests = [test for test in var_parse_tests if test.endswith(".env")]
var_parse_tests.sort()

var_parse_failures = set()
var_parse_skipped = set()

for test_name in var_parse_tests:
    bash_version = (5, 0, 17) if "old" in test_name else (5, 2, 32)
    test = os.path.join(TEST_VAR_PARSE_PATH, test_name)
    logging.info(f'Test: {test_name}')

    skip_test = test_name.startswith("skip")
    if skip_test:
        logging.info(f'Skipping...')
        var_parse_skipped.add(test_name)
        continue

    expected_var_file = os.path.join(TEST_VAR_PARSE_PATH, test_name.replace(".env",".json"))
    expected_success = os.path.exists(expected_var_file)

    expected = None
    if expected_success:
        with open(expected_var_file) as f:
            expected = json.load(f)
        expected = {k: tuple(v) for k, v in expected.items()}

    try:
        got = env_vars_util.read_vars_file(test, bash_version_tuple=bash_version)
    except ValueError:
        if expected_success:
            var_parse_failures.add(test_name)
            print("Found unexpected failure in", test_name)
            print("Error: ", traceback.format_exc())
        else:
            print("Found expected failure in", test_name)
    except Exception as e:
       print(f"Error in {test_name}:", traceback.format_exc())
       var_parse_failures.add(test_name)
    else:
        logging.info(f"Variables: {got}")
        success = expected == got
        if success and not expected_success:
            print("Unexpected success in", test_name)
            print(got)
            var_parse_failures.add(test_name)
        elif not success and expected_success:
            print(f"In {test_name} expected vs got: ")
            exp_keys = list(expected.keys())
            exp_keys.sort()
            got_keys = list(got.keys())
            got_keys.sort()
            assert exp_keys == got_keys

            for k in exp_keys:
                if expected[k] != got[k]:
                    print(f"for {k}, expected {expected[k]}, got {got[k]}")
            var_parse_failures.add(test_name)
       
if len(var_parse_failures) > 0:
    test_success = False

print_report(var_parse_tests, var_parse_failures, var_parse_skipped)

if test_success:
    exit(0)
else:
    exit(1)
