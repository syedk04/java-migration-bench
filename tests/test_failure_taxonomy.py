"""Tests for S18: failure taxonomy classifier (no file I/O needed)."""

from migration_agent.failure_taxonomy import _classify_record, _classify_text


def test_classify_pass():
    r = {"minimal": True, "maximal": False}
    assert _classify_record(r) == "PASS"


def test_classify_clone_error_from_error_field():
    r = {"minimal": False, "error": "Command 'git clone ... failed with return code 128"}
    assert _classify_record(r) == "CLONE_ERROR"


def test_classify_bytecode_wrong():
    r = {"minimal": False, "r1": True, "r2": False}
    assert _classify_record(r) == "BYTECODE_WRONG"


def test_classify_tampered():
    r = {"minimal": False, "r1": True, "r2": True, "tampered": True}
    assert _classify_record(r) == "TAMPERED"


def test_classify_jaxb_from_maven_output():
    r = {"minimal": False, "r1": False, "r2": False}
    maven = "ERROR: package javax.xml.bind does not exist"
    assert _classify_record(r, maven) == "JAXB_MISSING"


def test_classify_nashorn():
    r = {"minimal": False}
    maven = "[ERROR] Cannot find nashorn ScriptEngine not found"
    assert _classify_record(r, maven) == "NASHORN_MISSING"


def test_classify_compile_error():
    r = {"minimal": False}
    maven = "[ERROR] COMPILATION ERROR\n[ERROR] 5 errors"
    assert _classify_record(r, maven) == "COMPILE_ERROR"


def test_classify_test_failure():
    r = {"minimal": False}
    maven = "[ERROR] Tests run: 10, Failures: 2, Errors: 0, Skipped: 0"
    assert _classify_record(r, maven) == "TEST_FAILURE"


def test_classify_javax_jakarta():
    r = {"minimal": False}
    maven = "[ERROR] package javax.servlet does not exist"
    assert _classify_record(r, maven) == "JAVAX_JAKARTA"


def test_classify_reflection():
    r = {"minimal": False}
    maven = "InaccessibleObjectException: Unable to make field accessible: module java.base"
    assert _classify_record(r, maven) == "REFLECTION"


def test_classify_text_no_match():
    assert _classify_text("Everything is fine") == "UNKNOWN"


def test_classify_unknown_failure():
    r = {"minimal": False, "r1": False, "r2": False}
    assert _classify_record(r) == "BUILD_FAIL_UNKNOWN"
